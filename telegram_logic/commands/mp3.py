"""
/mp3 <terabox_link> — extract audio from a TeraBox video as MP3.

Leaner path than /exp: no cache group, no storage forwarding —
resolve → download → ffmpeg convert → send audio directly.
"""
import asyncio
import os
import threading
import subprocess

from telethon import events
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename

from ..bot import bot, _safe_send, shutting_down, active_tasks, acquire_user_slot, release_user_slot, USER_MAX_CONCURRENT
from ..helpers import env_int, format_size, check_size_limit
from .. import rate_limit
from firebase_db.stats import record_success as stats_ok, record_failure as stats_fail
from firebase_db.users import record_history, bump_today
from ..structured_log import ctx_logger, bind_context, new_request_id
from teraboxDL.errors import TeraBoxError, CancelledError, TeraBoxDirectError
from teraboxDL.public_api import download_terabox_file_experimental
from teraboxDL.terabox_dl import get_video_info

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")


def _convert_to_mp3(mp4_path: str) -> str:
    """ffmpeg video -> mp3 (V5 ~130kbps). Raises TeraBoxError on failure."""
    mp3_path = os.path.splitext(mp4_path)[0] + ".mp3"
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", mp4_path,
                "-vn", "-acodec", "libmp3lame", "-q:a", "5",
                mp3_path,
            ],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired as e:
        raise TeraBoxError("Audio conversion timed out — the video may be too long.") from e
    except FileNotFoundError as e:
        raise TeraBoxError("ffmpeg is not available on this server.") from e

    if proc.returncode != 0 or not os.path.exists(mp3_path) or os.path.getsize(mp3_path) < 1024:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise TeraBoxError(f"Audio conversion failed: {detail[0][:120]}")
    return mp3_path


def _strip_ext(name: str) -> str:
    return os.path.splitext(name)[0]


@bot.on(events.NewMessage(pattern=r"^/mp3(?:\s+(.+))?$"))
async def cmd_mp3(event):
    if shutting_down.is_set():
        await _safe_send(event.respond, "🛑 Bot is restarting — please try again in a minute.")
        return

    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        await _safe_send(event.respond, "Usage: `/mp3 <link>`\n\nExample: `/mp3 https://1024tera.com/s/1abc…`")
        return

    chat_id = event.chat_id
    rid = new_request_id()
    bind_context(request_id=rid, user_id=chat_id, download_id=rid)

    blocked = rate_limit.check_rate_limit(chat_id)
    if blocked:
        await _safe_send(event.respond, blocked)
        return

    is_admin = bool(ADMIN_ID and chat_id == ADMIN_ID)
    if not acquire_user_slot(chat_id, is_admin=is_admin):
        await _safe_send(
            event.respond,
            f"⏳ You already have **{USER_MAX_CONCURRENT}** download(s) running. "
            "Please wait for them to finish.",
        )
        return

    task_key = f"mp3-{chat_id}-{rid}"
    cancel_event = threading.Event()
    active_tasks[task_key] = {"cancel": cancel_event, "chat_id": chat_id}

    status = None
    try:
        status = await _safe_send(event.respond, "🔎 Resolving link…")

        info = await asyncio.to_thread(get_video_info, arg, False)
        filename = info["filename"]
        download_url = info["download_url"]

        if not download_url:
            raise TeraBoxDirectError("No downloadable stream found for this link.")

        size_note = ""
        if info.get("size"):
            over = check_size_limit(info["size"])
            if over:
                raise TeraBoxDirectError(over)
            size_note = f" ({format_size(info['size'])})"

        await _safe_send(status.edit, f"🎬 **{filename}**{size_note}\n⬇️ Downloading video…")

        dl_progress_cb = None  # keep simple: single status line per stage
        filepath = await asyncio.to_thread(
            download_terabox_file_experimental,
            download_url, filename, cancel_event, dl_progress_cb,
        )

        await _safe_send(status.edit, "🎵 Extracting audio…")
        mp3_path = await asyncio.to_thread(_convert_to_mp3, filepath)

        mp3_size = os.path.getsize(mp3_path)
        title = _strip_ext(filename)
        await _safe_send(status.edit, f"📤 Uploading {format_size(mp3_size)}…")

        await bot.send_file(
            event.chat_id,
            mp3_path,
            attributes=[
                DocumentAttributeAudio(title=title[:64], duration=0),
                DocumentAttributeFilename(f"{title[:60]}.mp3"),
            ],
            supports_streaming=True,
            caption=f"🎵 **{title}**",
        )

        _cleanup(filepath, mp3_path)
        rate_limit.register_success(chat_id)
        stats_ok(mp3_size)
        await asyncio.to_thread(record_history, chat_id, f"{title}.mp3", f"mp3:{filename}", mp3_size)
        await asyncio.to_thread(bump_today, chat_id)
        try:
            await _safe_send(status.delete)
        except Exception:
            pass
        log.info("mp3 delivered", extra={"title": title, "bytes": mp3_size})

    except CancelledError:
        _cleanup(locals().get("filepath"), locals().get("mp3_path"))
        await _safe_send(event.respond, "🚫 Download cancelled.")
        log.info("mp3 cancelled by user")
    except (TeraBoxError, TeraBoxDirectError) as e:
        _cleanup(locals().get("filepath"), locals().get("mp3_path"))
        rate_limit.register_failure(chat_id)
        stats_fail()
        await _safe_send(status.edit if status else event.respond, f"❌ {e}")
        log.error("mp3 pipeline error", extra={"error": str(e)})
    except Exception:
        _cleanup(locals().get("filepath"), locals().get("mp3_path"))
        rate_limit.register_failure(chat_id)
        stats_fail()
        log.exception("unexpected mp3 error")
        await _safe_send(status.edit if status else event.respond, "❌ Unexpected error. Try again shortly.")
    finally:
        active_tasks.pop(task_key, None)
        release_user_slot(chat_id, is_admin)


def _cleanup(*paths: str | None) -> None:
    from pathlib import Path as _P
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
                log.debug("cleaned up file", extra={"path": str(_P(p).name)})
            except OSError as e:
                log.warning("cleanup failed", extra={"path": p, "error": str(e)})
