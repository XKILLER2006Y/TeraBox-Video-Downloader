"""
Download a video from a stream URL.

Handles two cases:
1. Direct file URLs (.mp4, .mkv, .webm, etc.) -> downloaded with requests, chunked.
2. Streaming manifests (.m3u8 HLS) -> segments downloaded individually with
   requests and concatenated, then optionally remuxed with ffmpeg.

The segment-based approach is needed because TeraBox proxies HLS segments
through Cloudflare workers (e.g. https://worker.dev?url=...), which ffmpeg
refuses to fetch because the URLs don't end in .ts.

Requirements:
    pip install requests
    ffmpeg on PATH is optional (used for remuxing TS -> MP4 container)
"""

import os
import logging
import subprocess
import shutil
import threading
import time
from urllib.parse import urlparse, urljoin

import requests

log = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Headers WITHOUT Accept-Encoding to avoid brotli responses that
# requests can't decode without the brotli package installed.
_PLAIN_HEADERS = {
    "User-Agent": _BROWSER_HEADERS["User-Agent"],
    "Accept": "*/*",
    "Connection": "keep-alive",
}

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB


def is_streaming_manifest(url: str) -> bool:
    """
    Check if the URL points to an HLS (.m3u8) or DASH (.mpd) manifest.

    Detection strategy (in order):
    1. File extension: URL path ends with .m3u8 or .mpd
    2. Path keywords: URL path contains 'm3u8', 'hls', or 'mpd'/'dash'
    3. Content-Type probe: HEAD request to check MIME type
    """
    parsed = urlparse(url)
    path = parsed.path.lower()

    # 1. Explicit file extension
    if path.endswith(".m3u8") or path.endswith(".mpd"):
        return True

    # 2. URL path contains streaming keywords
    streaming_keywords = ("m3u8", "hls", ".mpd", "dash")
    if any(kw in path for kw in streaming_keywords):
        return True

    # 3. Probe Content-Type via HEAD request (lightweight)
    try:
        r = requests.head(url, headers=_PLAIN_HEADERS, timeout=10, allow_redirects=True)
        content_type = r.headers.get("Content-Type", "").lower()
        hls_types = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl")
        dash_types = ("application/dash+xml",)
        if any(ct in content_type for ct in hls_types + dash_types):
            return True
    except Exception:
        pass  # If probe fails, assume it's a direct file

    return False


def _parse_m3u8_segments(manifest_text: str, manifest_url: str) -> list[str]:
    """
    Parse an m3u8 playlist and extract segment URLs.
    Handles both absolute and relative segment URLs.
    """
    segments = []
    for line in manifest_text.strip().splitlines():
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue
        # Resolve relative URLs against the manifest URL
        if line.startswith("http://") or line.startswith("https://"):
            segments.append(line)
        else:
            segments.append(urljoin(manifest_url, line))
    return segments


def _download_direct_file(
    url: str,
    output_file: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> None:
    """Download a direct video file with streaming + progress reporting."""
    log.info(f"Downloading direct file from: {url}")

    with requests.get(url, headers=_BROWSER_HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(output_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if cancel_event and cancel_event.is_set():
                    raise Exception("Download cancelled")
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)

                if total:
                    pct = downloaded / total * 100
                    log.info(
                        f"Downloading: {pct:5.1f}% "
                        f"({downloaded / 1e6:.1f} MB / {total / 1e6:.1f} MB)"
                    )
                else:
                    log.info(f"Downloading: {downloaded / 1e6:.1f} MB")

                if progress_callback:
                    progress_callback(downloaded, total)

    log.info(f"Direct download saved to {output_file}")


def _download_hls_segments(
    manifest_url: str,
    output_file: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> None:
    """Fetch a remote m3u8 manifest and download all segments."""
    log.info(f"Fetching m3u8 manifest from: {manifest_url}")
    r = requests.get(manifest_url, headers=_PLAIN_HEADERS, timeout=30)
    r.raise_for_status()
    manifest_text = r.text
    log.info(f"Manifest content ({len(manifest_text)} bytes):\n{manifest_text[:200]}...")

    _download_hls_from_manifest(manifest_text, manifest_url, output_file, cancel_event, progress_callback)


def _download_hls_segments_local(
    m3u8_path: str,
    output_file: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> None:
    """Read a LOCAL m3u8 playlist file and download all segments listed in it."""
    log.info(f"Reading local m3u8 playlist: {m3u8_path}")
    with open(m3u8_path, "r") as f:
        manifest_text = f.read()

    first_seg = ""
    for line in manifest_text.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            first_seg = line
            break

    _download_hls_from_manifest(manifest_text, first_seg, output_file, cancel_event, progress_callback)


def _download_hls_from_manifest(
    manifest_text: str,
    base_url: str,
    output_file: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> None:
    """Core HLS download logic - shared by remote and local manifest paths."""
    segment_urls = _parse_m3u8_segments(manifest_text, base_url)
    if not segment_urls:
        raise Exception("No segments found in m3u8 manifest")
    log.info(f"Found {len(segment_urls)} segments in manifest")

    ts_output = output_file + ".ts"
    total_downloaded = 0
    start_time = time.time()

    try:
        with open(ts_output, "wb") as out_f:
            for i, seg_url in enumerate(segment_urls):
                if cancel_event and cancel_event.is_set():
                    raise Exception("Download cancelled")

                log.info(f"Downloading segment {i + 1}/{len(segment_urls)}")

                for attempt in range(3):
                    try:
                        seg_r = requests.get(
                            seg_url,
                            headers=_PLAIN_HEADERS,
                            stream=True,
                            timeout=60,
                        )
                        seg_r.raise_for_status()

                        for chunk in seg_r.iter_content(chunk_size=CHUNK_SIZE):
                            if cancel_event and cancel_event.is_set():
                                raise Exception("Download cancelled")
                            if not chunk:
                                continue
                            out_f.write(chunk)
                            total_downloaded += len(chunk)

                            elapsed = time.time() - start_time
                            speed = (total_downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            print(
                                f"\r    Downloading: {total_downloaded / (1024 * 1024):.2f} MB  "
                                f"{speed:.1f} MB/s  "
                                f"[segment {i + 1}/{len(segment_urls)}]",
                                end="", flush=True,
                            )

                            if progress_callback:
                                progress_callback(total_downloaded, 0)

                        break
                    except Exception as e:
                        if "cancelled" in str(e).lower():
                            raise
                        if attempt == 2:
                            raise Exception(
                                f"Segment {i + 1} failed after 3 attempts: {e}"
                            )
                        log.warning(f"Segment {i + 1} attempt {attempt + 1} failed: {e}, retrying...")
                        time.sleep(1 + attempt)

        print()
        log.info(f"All segments downloaded: {ts_output} ({total_downloaded / 1e6:.2f} MB)")

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path and output_file.lower().endswith(".mp4"):
            log.info("Remuxing TS -> MP4 with ffmpeg...")
            cmd = [
                ffmpeg_path, "-y", "-i", ts_output,
                "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", output_file,
            ]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                log.info(f"Remuxed to {output_file}")
                os.remove(ts_output)
                return
            else:
                stderr = result.stderr.decode("utf-8", errors="replace")
                log.warning(f"ffmpeg remux failed: {stderr[-300:]}")

        if os.path.exists(ts_output):
            if os.path.exists(output_file):
                os.remove(output_file)
            os.rename(ts_output, output_file)
            log.info(f"Saved as {output_file} (TS container, no remux)")

    except Exception:
        if os.path.exists(ts_output):
            try:
                os.remove(ts_output)
            except Exception:
                pass
        raise


def download_from_stream_url(
    stream_url: str,
    output_file: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> str:
    """
    Download a video from a stream URL.

    Automatically detects whether the URL is an HLS/DASH manifest or a
    direct file link and uses the appropriate download strategy.

    Also supports local M3U8 file paths (from chunk discovery).

    Args:
        stream_url:        The stream URL or local M3U8 file path.
        output_file:       Path where the downloaded file will be saved.
        cancel_event:      Optional threading.Event for cancellation.
        progress_callback: Optional callback(downloaded_bytes, total_bytes).

    Returns:
        The absolute path to the downloaded file.

    Raises:
        Exception on any download failure.
    """
    if not stream_url:
        raise Exception("stream_url is empty - cannot download.")

    # Ensure output directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Check if stream_url is a local file path (M3U8 from chunk discovery)
    if not stream_url.startswith("http") and os.path.isfile(stream_url):
        log.info(f"Detected local M3U8 playlist: {stream_url}")
        if not output_file.lower().endswith(".mp4"):
            output_file = os.path.splitext(output_file)[0] + ".mp4"
        _download_hls_segments_local(stream_url, output_file, cancel_event, progress_callback)
    elif is_streaming_manifest(stream_url):
        log.info(f"Detected streaming manifest URL: {stream_url[:50]}")
        if not output_file.lower().endswith(".mp4"):
            output_file = os.path.splitext(output_file)[0] + ".mp4"
        _download_hls_segments(stream_url, output_file, cancel_event, progress_callback)
    else:
        log.info(f"Detected direct file URL: {stream_url[:50]}")
        _download_direct_file(stream_url, output_file, cancel_event, progress_callback)

    # Validate the download
    if not os.path.exists(output_file):
        raise Exception(f"Download failed: output file not found at {output_file}")

    file_size = os.path.getsize(output_file)
    if file_size < 512:
        os.remove(output_file)
        raise Exception(f"Download failed: file too small ({file_size} bytes)")

    log.info(f"Download complete: {output_file} ({file_size / 1e6:.2f} MB)")
    return os.path.abspath(output_file)
