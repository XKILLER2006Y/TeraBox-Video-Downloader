"""
/status — bot health dashboard.

Public summary: uptime, state, active downloads, queue depth, flood cooldown,
memory, storage. Admin detail (cookie pool health, rate-limit state) is shown
only to ADMIN_ID when set.
"""
import os
import time
import logging
from telethon import events

from ..bot import bot, active_tasks, terabox_queue, shutting_down, START_TIME
from ..helpers import env_int
from .. import rate_limit
from teraboxDL.terabox_dl import cookie_pool_health

log = logging.getLogger(__name__)

ADMIN_ID = env_int("ADMIN_ID")


def _fmt_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) or f"{secs}s"


def _memory_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return 0.0


def _storage_dir_mb() -> float:
    try:
        total = sum(
            os.path.getsize(os.path.join("storage", f))
            for f in os.listdir("storage")
            if os.path.isfile(os.path.join("storage", f))
        )
        return total / (1024 * 1024)
    except Exception:
        return 0.0


def build_status_text(is_admin: bool) -> str:
    uptime = time.time() - START_TIME
    flood = terabox_queue.flood_remaining()

    lines = [
        "📊 **Bot Status**",
        "",
        f"🟢 State: {'🟡 draining (restarting)' if shutting_down.is_set() else 'Running'}",
        f"⏱️ Uptime: **{_fmt_uptime(uptime)}**",
        f"⚙️ Active downloads: **{len(active_tasks)}**",
        f"📥 Queued (flood): **{terabox_queue.pending}**",
    ]
    if flood > 0:
        lines.append(f"🌊 Flood cooldown: **{flood}s** remaining")

    mem = _memory_mb()
    if mem:
        lines.append(f"🧠 Memory (peak RSS): **{mem:.0f} MB**")
    disk = _storage_dir_mb()
    if disk:
        lines.append(f"💾 Storage dir: **{disk:.1f} MB**")

    if is_admin:
        lines += ["", "🔧 **Admin detail**"]
        cookies = cookie_pool_health()
        if cookies:
            ok = sum(1 for c in cookies if c["state"] == "ok")
            bad = sum(1 for c in cookies if c["state"] == "bad")
            unknown = len(cookies) - ok - bad
            lines.append(
                f"🍪 Cookie pool: {len(cookies)} configured — "
                f"ok: {ok}, bad/expired: {bad}, unvalidated: {unknown}"
            )
        else:
            lines.append("🍪 Cookie pool: none configured (anonymous mode)")
        rl = rate_limit.stats()
        lines.append(
            f"🚦 Rate-limit: {rl['currently_blocked']} blocked, "
            f"{rl['tracked_users_with_failures']} with failure history"
        )

    return "\n".join(lines)


@bot.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    log.info(f"Received /status command from chat {event.chat_id}")
    is_admin = bool(ADMIN_ID and event.chat_id == ADMIN_ID)
    await event.respond(build_status_text(is_admin))
    raise events.StopPropagation
