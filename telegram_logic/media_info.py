"""
telegram_logic/media_info.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Utilities to extract video duration, width, and height using ffprobe,
generate clean video preview thumbnails using ffmpeg, and build Telethon
DocumentAttributeVideo objects so uploaded videos render as native playable
videos with seekable player controls in Telegram.
"""
import os
import json
import subprocess
import logging
from telethon.tl.types import DocumentAttributeVideo

log = logging.getLogger(__name__)


def extract_video_metadata(file_path: str) -> dict:
    """
    Extract width, height, and duration (in seconds) from a video file using ffprobe.
    Returns: {"width": int, "height": int, "duration": int}
    """
    if not file_path or not os.path.exists(file_path):
        return {"width": 0, "height": 0, "duration": 0}

    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=width,height,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            duration = 0
            width = 0
            height = 0

            # Scan streams for video track
            for s in data.get("streams", []):
                if s.get("width") and s.get("height"):
                    width = int(s["width"])
                    height = int(s["height"])
                if s.get("duration"):
                    try:
                        duration = int(float(s["duration"]))
                    except Exception:
                        pass

            # Fall back to container format duration if stream duration wasn't present
            if duration <= 0 and data.get("format", {}).get("duration"):
                try:
                    duration = int(float(data["format"]["duration"]))
                except Exception:
                    pass

            return {"width": width, "height": height, "duration": duration}
    except Exception as e:
        log.warning(f"ffprobe extraction failed for {file_path}: {e}")

    return {"width": 0, "height": 0, "duration": 0}


def generate_video_thumbnail(file_path: str, output_thumb_path: str | None = None) -> str | None:
    """
    Generate a JPEG thumbnail image from a video file using ffmpeg.
    Captures a frame at 1s (or start) scaled to a max width of 320px.
    Returns the path to the thumbnail file, or None on failure.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    if output_thumb_path is None:
        base, _ = os.path.splitext(file_path)
        output_thumb_path = f"{base}_thumb.jpg"

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", "00:00:01",
            "-i", file_path,
            "-vframes", "1",
            "-vf", "scale='min(320,iw)':-2",
            "-q:v", "2",
            output_thumb_path,
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if res.returncode == 0 and os.path.exists(output_thumb_path) and os.path.getsize(output_thumb_path) > 0:
            return output_thumb_path
    except Exception as e:
        log.warning(f"ffmpeg thumbnail generation failed for {file_path}: {e}")

    return None


def get_video_attributes(
    file_path: str,
    duration: int | dict | None = None,
    width: int | None = None,
    height: int | None = None,
) -> list[DocumentAttributeVideo]:
    """
    Return a list containing a DocumentAttributeVideo configured with duration,
    width, height, and streaming support for Telegram.
    """
    if isinstance(duration, dict):
        meta = duration
        duration = meta.get("duration", 0)
        width = width if width is not None else meta.get("width", 0)
        height = height if height is not None else meta.get("height", 0)
    elif duration is None or width is None or height is None:
        meta = extract_video_metadata(file_path)
        duration = duration if duration is not None else meta.get("duration", 0)
        width = width if width is not None else meta.get("width", 0)
        height = height if height is not None else meta.get("height", 0)

    return [
        DocumentAttributeVideo(
            duration=int(duration or 0),
            w=int(width or 0),
            h=int(height or 0),
            supports_streaming=True,
        )
    ]
