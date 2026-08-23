import threading
import os
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from .internal_helpers import _safe_filename, BYTES_PER_MB, _headers
from .core_pipeline import get_js_token, get_share_info, discover_all_hls_chunks, download_all_chunks, concatenate_chunks_ffmpeg, build_streaming_url
from .internal_helpers import TeraBoxError, CancelledError
from network import get_session

# ── Config ────────────────────────────────────────────────────────────────────

STORAGE_DIR = "storage"
QUALITY = "M3U8_AUTO_1080"

# ── Public API ─────────────────────────────────────────────────────────

def prepare_terabox_link(surl: str) -> dict:
    """
    Fetch file metadata for a TeraBox SURL.

    Returns a dict with keys:
        filename, size, fs_id, shareid, uk, sign, timestamp, session, surl

    Raises TeraBoxError on any failure.
    """
    session = get_session()

    print("[1] Extracting jsToken...")
    js_token = get_js_token(session, surl)
    print(f"    jsToken: {js_token[:30]}...")

    print("[2] Fetching share info...")
    info = get_share_info(session, js_token, surl)

    files = info.get("list", [])
    if not files:
        print("    No files in share.")
        raise TeraBoxError("No files found in this share")
        
    print(f"    Found {len(files)} file(s)\n")
    f = files[0]
    
    return {
        "filename": f["server_filename"],
        "size": int(f.get("size", 0)),
        "fs_id": f["fs_id"],
        "shareid": info["shareid"],
        "uk": info["uk"],
        "sign": info["sign"],
        "timestamp": info["timestamp"],
        "session": session,
        "surl": surl,
    }


def download_terabox_file(
    prepared: dict,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> str:
    """
    Download the video described by `prepared` (from prepare_terabox_link).

    Returns the absolute path to a local MP4 file.
    Raises TeraBoxError or CancelledError.
    """
    surl = prepared["surl"]
    session = prepared["session"]
    filename = prepared["filename"]
    size = prepared["size"]

    safe = _safe_filename(filename)
    # Hash-prefix prevents same-named files from different shares colliding
    # on disk when processed concurrently.
    url_hash = hashlib.sha256(surl.encode()).hexdigest()[:8]
    safe = f"{url_hash}_{safe}"
    os.makedirs(STORAGE_DIR, exist_ok=True)
    mp4_path = os.path.join(STORAGE_DIR, safe if safe.lower().endswith(".mp4") else safe + ".mp4")
    tmp_dir = os.path.join(STORAGE_DIR, safe.rsplit(".", 1)[0] + "_segments")

    # Re-use an already-downloaded local copy to avoid re-downloading
    if os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 1024:
        print(f"    Done! {mp4_path}")
        return mp4_path

    print(f"  [1] {filename} ({size / BYTES_PER_MB:.1f} MB)")

    try:
        working_quality = None
        qualities_to_try = [
            "M3U8_AUTO_1080", "M3U8_AUTO_720",
            "M3U8_AUTO_480", "M3U8_AUTO_360",
            "M3U8_720P", "M3U8_480P", "M3U8_360P"
        ]

        def _probe_quality(q):
            url = build_streaming_url(
                prepared["shareid"], prepared["uk"], prepared["sign"],
                prepared["timestamp"], prepared["fs_id"], q
            )
            try:
                r = session.get(url, headers=_headers(session, surl), timeout=10)
                if r.text.strip().startswith("#EXTM3U"):
                    return q
            except Exception:
                pass
            return None

        # Probe top 3 in parallel (fast timeout), then fall back to rest
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_probe_quality, q): q for q in qualities_to_try[:3]}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    working_quality = result
                    break
            if not working_quality:
                # Cancel remaining and probe rest
                for f in futures:
                    f.cancel()
                for q in qualities_to_try[3:]:
                    result = _probe_quality(q)
                    if result:
                        working_quality = result
                        break

        if not working_quality:
            raise TeraBoxError("Could not find any available streaming quality for this video.")

        print(f"    Using quality {working_quality} for download...")
        
        # Step 1: Scan for all distinct TS chunks spanning the video
        chunks = discover_all_hls_chunks(
            session, prepared["shareid"], prepared["uk"], 
            prepared["sign"], prepared["timestamp"], prepared["fs_id"], 
            working_quality, surl=surl, cancel_event=cancel_event
        )
        
        # Step 2: Download every chunk
        download_all_chunks(session, chunks, tmp_dir, surl=surl, cancel_event=cancel_event, progress_callback=progress_callback)
        
        # Step 3: Concat & Remux to MP4
        concatenate_chunks_ffmpeg(tmp_dir, chunks, mp4_path, cancel_event=cancel_event)
        
        final_size = os.path.getsize(mp4_path) / BYTES_PER_MB
        print(f"    Done! {mp4_path}. Video Size: {final_size:.1f} MB")
        
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return mp4_path

    except Exception as e:
        print(f"    Failed: {e}")
        # Clean up partial files
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(mp4_path) and os.path.getsize(mp4_path) < 1024:
            os.remove(mp4_path)
            
        if isinstance(e, CancelledError):
            raise
        raise TeraBoxError(f"Download failed: {e}") from e
