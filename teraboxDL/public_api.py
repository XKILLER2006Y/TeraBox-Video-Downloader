import threading
import os
import hashlib
import logging
import requests
import time
import random
import shutil
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from terabox.internal_helpers import _safe_filename, TeraBoxError, CancelledError
from teraboxDL.stream_downloader import is_streaming_manifest, download_from_stream_url
from network import get_session

log = logging.getLogger(__name__)

STORAGE_DIR = "storage"
CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB per read chunk within each part

# Number of parallel parts to split a single download into.
# 4 connections → ~4x throughput on CDNs that allow range requests.
PARALLEL_PARTS = 4

# Module-level executor — reused across downloads to avoid thread creation overhead
_dl_executor = ThreadPoolExecutor(max_workers=PARALLEL_PARTS, thread_name_prefix="terabox-dl")

# Browser-identical headers — this is the #1 reason for throttling.
# TeraBox CDN checks User-Agent and throttles python-requests to ~100KB/s.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def _build_session() -> requests.Session:
    """Return the global session singleton. Kept for backward compat."""
    return get_session()


def download_terabox_file_experimental(
    download_url: str,
    filename: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> str:
    """
    Download the video described by download_url and filename.
    
    Returns the absolute path to a local MP4 file.
    Raises TeraBoxError or CancelledError.
    
    Includes error handling for zero files and 0KB failures.
    """
    
    safe = _safe_filename(filename)
    # Prefix with a short hash of the source URL so that two different
    # shares with identical filenames never collide on disk when processed
    # concurrently (interleaved writes + wrong-file cleanup).
    url_hash = hashlib.sha256(download_url.encode()).hexdigest()[:8]
    safe = f"{url_hash}_{safe}"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    mp4_path = os.path.join(STORAGE_DIR, safe if safe.lower().endswith(".mp4") else safe + ".mp4")
    
    # Check if download URL is valid
    if not download_url:
        raise TeraBoxError("No download URL provided - the link may be expired or invalid")
    
    # Check for empty M3U8 manifests
    if "#EXTM3U" in download_url and len(download_url.strip().split("\n")) < 3:
        raise TeraBoxError("Empty M3U8 manifest - the video may have been removed or is no longer available")
    
    try:
        if is_streaming_manifest(download_url):
            # Use ffmpeg-based stream downloader for HLS/DASH manifests
            log.info("Detected HLS/DASH manifest, using ffmpeg...")
            download_from_stream_url(download_url, mp4_path, cancel_event, progress_callback)
        else:
            _download_video(download_url, mp4_path, cancel_event, progress_callback)
        
        # Verify downloaded file exists and has content
        if not os.path.exists(mp4_path):
            raise TeraBoxError("Download completed but no file was created")
        
        file_size = os.path.getsize(mp4_path)
        if file_size < 1024:
            # File too small - likely failed
            os.remove(mp4_path)
            raise TeraBoxError(f"Downloaded file too small ({file_size} bytes) - the video may be corrupted or unavailable")
        
        log.info(f"Download Completed! {mp4_path} ({file_size / (1024*1024):.1f} MB)")
        return mp4_path
        
    except Exception as e:
        log.error(f"Download failed: {e}")
        if os.path.exists(mp4_path):
            try:
                os.remove(mp4_path)
            except OSError:
                pass
            
        if isinstance(e, CancelledError):
            raise
        raise TeraBoxError(f"Download failed: {e}") from e

#!--------------PRIVATE HELPERS----------------

def _check_range_support(session: requests.Session, download_url: str) -> int:
    """
    HEAD the URL to get content-length and check if the server supports
    HTTP Range requests. Returns total_size (0 if unknown/no range support).
    """
    try:
        r = session.head(download_url, timeout=15, allow_redirects=True)
        r.raise_for_status()
        accepts_ranges = r.headers.get("Accept-Ranges", "").lower()
        content_length = int(r.headers.get("Content-Length", 0))
        if accepts_ranges == "bytes" and content_length > 0:
            return content_length
    except Exception:
        pass
    return 0  # fallback → single-stream download


def _download_part(
    download_url: str,
    byte_start: int,
    byte_end: int,
    part_path: str,
    part_index: int,
    progress_lock: threading.Lock,
    shared_progress: list,          # [done_bytes]
    total_size: int,
    start_time: float,
    cancel_event: threading.Event | None,
    progress_callback,
) -> None:
    """Download a single byte-range part of the file to part_path."""
    # Each thread gets its own session to avoid header mutation race on singleton
    part_session = get_session()
    parsed = urlparse(download_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    headers = {"Range": f"bytes={byte_start}-{byte_end}", "Referer": referer}
    for attempt in range(4):
        if cancel_event and cancel_event.is_set():
            raise CancelledError("Download cancelled")
        try:
            r = part_session.get(download_url, headers=headers, stream=True, timeout=120)
            r.raise_for_status()
            with open(part_path, "wb") as f:
                for chunk in r.iter_content(CHUNK_SIZE):
                    if cancel_event and cancel_event.is_set():
                        raise CancelledError("Download cancelled")
                    f.write(chunk)
                    with progress_lock:
                        shared_progress[0] += len(chunk)
                        done = shared_progress[0]

                    elapsed = time.time() - start_time
                    speed = (done / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    done_mb = done / (1024 * 1024)
                    if total_size > 0:
                        total_mb = total_size / (1024 * 1024)
                        pct = (done / total_size) * 100
                        log.debug(
                            f"Downloading: {done_mb:.2f} / {total_mb:.2f} MB"
                            f"  ({pct:.0f}%)  {speed:.1f} MB/s  [part {part_index+1}/{PARALLEL_PARTS}]"
                        )
                    else:
                        log.debug(f"Downloading: {done_mb:.2f} MB  {speed:.1f} MB/s [part {part_index+1}/{PARALLEL_PARTS}]")

                    if progress_callback:
                        progress_callback(done, total_size)
            return  # success
        except CancelledError:
            raise
        except Exception as e:
            if attempt == 3:
                raise TeraBoxError(f"Part {part_index} failed after 4 attempts: {e}")
            backoff = (2 ** attempt) + random.uniform(0.5, 2.0)
            log.info(f"[Part {part_index} retry {attempt+1} – sleep {backoff:.1f}s]")
            time.sleep(backoff)


def _download_video_multipart(
    session: requests.Session,
    download_url: str,
    download_path: str,
    total_size: int,
    cancel_event: threading.Event | None,
    progress_callback,
) -> None:
    """Split the file into PARALLEL_PARTS byte ranges and download concurrently."""
    part_size = total_size // PARALLEL_PARTS
    ranges = []
    for i in range(PARALLEL_PARTS):
        start = i * part_size
        end = (start + part_size - 1) if i < PARALLEL_PARTS - 1 else (total_size - 1)
        ranges.append((start, end))

    part_dir = download_path + ".parts"
    os.makedirs(part_dir, exist_ok=True)
    part_paths = [os.path.join(part_dir, f"part_{i}") for i in range(PARALLEL_PARTS)]

    progress_lock = threading.Lock()
    shared_progress = [0]  # mutable list so threads can update
    start_time = time.time()

    try:
        futures = {
            _dl_executor.submit(
                _download_part,
                download_url,
                ranges[i][0], ranges[i][1],
                part_paths[i],
                i,
                progress_lock,
                shared_progress,
                total_size,
                start_time,
                cancel_event,
                progress_callback,
            ): i
            for i in range(PARALLEL_PARTS)
        }
        for future in as_completed(futures):
            future.result()  # re-raise any exception from the part thread

        # Stitch parts together
        with open(download_path, "wb") as out:
            for part_path in part_paths:
                with open(part_path, "rb") as p:
                    shutil.copyfileobj(p, out)
    finally:
        # Clean up temp parts regardless of success/failure
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


def _download_video(
    download_url: str,
    download_path: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> None:
    session = _build_session()
    # Use a per-request Referer header (don't mutate singleton)
    parsed = urlparse(download_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"  # noqa: F841 — used in _download_part headers

    # Check if the CDN supports byte-range requests
    total_size = _check_range_support(session, download_url)

    if total_size > 0:
        log.info(f"[MultiPart] Server supports Range. Splitting into {PARALLEL_PARTS} parts ({total_size/(1024*1024):.1f} MB total).")
        _download_video_multipart(session, download_url, download_path, total_size, cancel_event, progress_callback)

        actual = os.path.getsize(download_path)
        if actual < total_size * 0.95:
            raise TeraBoxError(
                f"Incomplete download: got {actual} bytes, expected {total_size}"
            )
        return

    # ---- Fallback: single-stream download (server doesn't support Range) ----
    log.info("[SingleStream] Server does not support Range requests. Falling back to single stream.")
    for attempt in range(4):
        if cancel_event and cancel_event.is_set():
            raise CancelledError("Download cancelled")
        try:
            r = session.get(download_url, stream=True, timeout=120)
            r.raise_for_status()

            total_size = int(r.headers.get("content-length", 0))
            done_size = 0
            start_time = time.time()

            with open(download_path, "wb") as f:
                for chunk in r.iter_content(CHUNK_SIZE):
                    if cancel_event and cancel_event.is_set():
                        raise CancelledError("Download cancelled")
                    f.write(chunk)
                    done_size += len(chunk)

                    # Progress display (debug level — 1000+ lines for large files)
                    done_mb = done_size / (1024 * 1024)
                    elapsed = time.time() - start_time
                    speed = (done_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    if total_size > 0:
                        total_mb = total_size / (1024 * 1024)
                        pct = (done_size / total_size) * 100
                        log.debug(f"Downloading: {done_mb:.2f} / {total_mb:.2f} MB  ({pct:.0f}%)  {speed:.1f} MB/s")
                    else:
                        log.debug(f"Downloading: {done_mb:.2f} MB  {speed:.1f} MB/s")

                    if progress_callback:
                        progress_callback(done_size, total_size)

            actual = os.path.getsize(download_path)
            if actual < 512:
                raise TeraBoxError("Segment too small (< 512 bytes)")
            return
        except CancelledError:
            raise
        except Exception as e:
            if attempt == 3:
                raise TeraBoxError(f"Chunk failed after 4 attempts: {e}")

            backoff = (2 ** attempt) + random.uniform(1.0, 3.0)
            log.info(f"[Retry {attempt + 1} - sleep {backoff:.1f}s]")
            time.sleep(backoff)