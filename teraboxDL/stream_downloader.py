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
import requests
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

from network import get_session, _browser_headers as _BROWSER_HEADERS  # noqa: E402
from terabox.internal_helpers import CancelledError

log = logging.getLogger(__name__)

# Headers WITHOUT Accept-Encoding to avoid brotli responses that
# requests can't decode without the brotli package installed.
_PLAIN_HEADERS = {
    "User-Agent": _BROWSER_HEADERS["User-Agent"],
    "Accept": "*/*",
    "Connection": "keep-alive",
}

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB
HLS_PARALLEL_SEGMENTS = 4  # parallel segment download workers

# Module-level executor — reused across HLS downloads to avoid thread creation overhead
_hls_executor = ThreadPoolExecutor(max_workers=HLS_PARALLEL_SEGMENTS, thread_name_prefix="hls-dl")


def _build_session() -> requests.Session:
    """Return the global session singleton."""
    return get_session()


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
        r = get_session().head(url, headers=_PLAIN_HEADERS, timeout=10, allow_redirects=True)
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

    session = _build_session()
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(output_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if cancel_event and cancel_event.is_set():
                    raise CancelledError("Download cancelled")
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)

                if total:
                    pct = downloaded / total * 100
                    log.debug(
                        f"Downloading: {pct:5.1f}% "
                        f"({downloaded / 1e6:.1f} MB / {total / 1e6:.1f} MB)"
                    )
                else:
                    log.debug(f"Downloading: {downloaded / 1e6:.1f} MB")

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
    session = _build_session()
    r = session.get(manifest_url, timeout=30)
    r.raise_for_status()
    manifest_text = r.text
    log.debug(f"Manifest content ({len(manifest_text)} bytes):\n{manifest_text[:200]}...")

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

    num_segments = len(segment_urls)
    log.info(f"Found {num_segments} segments in manifest")
    ts_output = output_file + ".ts"
    part_dir = output_file + ".parts"
    os.makedirs(part_dir, exist_ok=True)
    part_paths = [os.path.join(part_dir, f"seg_{i}.ts") for i in range(num_segments)]

    progress_lock = threading.Lock()
    shared_progress = [0]
    start_time = time.time()

    def _download_segment(seg_url: str, seg_index: int) -> int:
        """Download a single segment to its temp file. Returns bytes downloaded."""
        session = _build_session()
        parsed = urlparse(seg_url)
        session.headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        for attempt in range(3):
            if cancel_event and cancel_event.is_set():
                raise CancelledError("Download cancelled")
            try:
                seg_r = session.get(seg_url, stream=True, timeout=60)
                seg_r.raise_for_status()
                seg_bytes = 0
                with open(part_paths[seg_index], "wb") as f:
                    for chunk in seg_r.iter_content(chunk_size=CHUNK_SIZE):
                        if cancel_event and cancel_event.is_set():
                            raise CancelledError("Download cancelled")
                        if not chunk:
                            continue
                        f.write(chunk)
                        seg_bytes += len(chunk)

                        with progress_lock:
                            shared_progress[0] += len(chunk)
                            done = shared_progress[0]

                        elapsed = time.time() - start_time
                        speed = (done / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                        log.debug(
                            f"Downloading: {done / (1024 * 1024):.2f} MB  "
                            f"{speed:.1f} MB/s  "
                            f"[{seg_index + 1}/{num_segments} segments]"
                        )
                        if progress_callback:
                            progress_callback(done, 0)
                return seg_bytes
            except CancelledError:
                raise
            except Exception as e:
                if attempt == 2:
                    raise Exception(
                        f"Segment {seg_index + 1} failed after 3 attempts: {e}"
                    )
                log.warning(f"Segment {seg_index + 1} attempt {attempt + 1} failed: {e}, retrying...")
                time.sleep(1 + attempt)
        return 0

    try:
        futures = {
            _hls_executor.submit(_download_segment, url, i): i
            for i, url in enumerate(segment_urls)
        }
        for future in as_completed(futures):
            future.result()  # re-raise any exception

        # Concatenate segments in order
        with open(ts_output, "wb") as out:
            for part_path in part_paths:
                with open(part_path, "rb") as p:
                    shutil.copyfileobj(p, out)

        total_downloaded = os.path.getsize(ts_output)
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
    finally:
        # Clean up temp segment files
        for pp in part_paths:
            if os.path.exists(pp):
                try:
                    os.remove(pp)
                except Exception:
                    pass
        if os.path.exists(part_dir):
            try:
                os.rmdir(part_dir)
            except Exception:
                pass


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

    Also supports M3U8 manifest text (from chunk discovery) and local file paths.

    Args:
        stream_url:        The stream URL, M3U8 text, or local M3U8 file path.
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

    # Detect M3U8 manifest text (from chunk discovery — no file path, no URL)
    if "#EXTM3U" in stream_url:
        log.info("Detected M3U8 manifest text (inline)")
        if not output_file.lower().endswith(".mp4"):
            output_file = os.path.splitext(output_file)[0] + ".mp4"
        # Skip temp file — pass manifest text directly to HLS downloader
        _download_hls_from_manifest(stream_url, "", output_file, cancel_event, progress_callback)
    # Check if stream_url is a local file path (M3U8 from chunk discovery)
    elif not stream_url.startswith("http") and os.path.isfile(stream_url):
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
