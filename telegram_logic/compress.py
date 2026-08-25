"""
Optional H.264 video compression (opt-in via `comp` keyword on /exp).

Re-encodes with ffmpeg at CRF 28 / preset veryfast — roughly halves file
size for typical phone-camera content while keeping playback quality
acceptable. Falls back to the original file when ffmpeg is unavailable,
errors out, or compression would not shrink meaningfully.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time

from teraboxDL.errors import CancelledError

from .structured_log import ctx_logger

log = ctx_logger(__name__)

# Only worth compressing above this size (bytes)
MIN_SIZE_FOR_COMPRESSION = 20 * 1024 * 1024


def _ffmpeg_compress(src: str, cancel_event: threading.Event | None) -> str | None:
    """
    Run the re-encode. Returns path of compressed output or None on any
    failure/cancellation. Output goes to <src>.c.mp4 next to the source.
    """
    dst = os.path.splitext(src)[0] + ".c.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostdin",
        "-i", src,
        "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        dst,
    ]
    # stderr goes to a temp file: a PIPE nobody drains can fill (~64KB) and
    # deadlock long encodes, even at -loglevel error.
    err_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_file)
    except OSError as e:
        err_file.close()
        log.warning("compression could not start", extra={"error": str(e)})
        return None

    # Poll for cancellation instead of blocking forever.
    while True:
        ret = proc.poll()
        if ret is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            proc.kill()
            proc.wait()
            err_file.close()
            if os.path.exists(dst):
                os.remove(dst)
            raise CancelledError("Download cancelled")
        time.sleep(0.5)

    if ret != 0:
        err_file.seek(0)
        stderr = err_file.read()[-300:]
        err_file.close()
        log.warning("compression failed", extra={"stderr": stderr})
        if os.path.exists(dst):
            os.remove(dst)
        return None

    err_file.close()
    return dst


def maybe_compress(filepath: str, cancel_event: threading.Event | None = None) -> tuple[str, str]:
    """
    Compress `filepath` if it makes sense. Returns (final_path, note) where
    note is a human-readable summary ("" when no compression happened).
    Never raises except CancelledError. Always cleans up the loser of the
    two files so disk does not fill up.
    """
    if not filepath or not os.path.exists(filepath):
        return filepath, ""

    size = os.path.getsize(filepath)
    if size < MIN_SIZE_FOR_COMPRESSION:
        return filepath, ""

    if shutil.which("ffmpeg") is None:
        log.warning("compression skipped: ffmpeg missing")
        return filepath, ""

    try:
        compressed = _ffmpeg_compress(filepath, cancel_event)
    except CancelledError:
        raise

    if not compressed or not os.path.exists(compressed):
        return filepath, ""

    new_size = os.path.getsize(compressed)
    if new_size >= size * 0.95:
        # Not worth it — keep original, drop the attempt
        os.remove(compressed)
        log.info("compression skipped: no meaningful savings",
                 extra={"orig": size, "new": new_size})
        return filepath, ""

    ratio = size / max(new_size, 1)
    note = f"🗜️ compressed {os.path.basename(filepath)} ({ratio:.1f}× smaller)"
    os.remove(filepath)  # keep the smaller one only
    return compressed, note
