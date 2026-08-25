"""
/stats — the user's own download dashboard.

Shows lifetime downloads + bytes, recent activity, current mode.
Admins additionally see global today/totals from /status's stats source.
"""
import asyncio

from telethon import events

from ..bot import bot
from ..helpers import env_int, format_size
from ..structured_log import ctx_logger
from firebase_db.stats import get_stats
from firebase_db.users import get_history, get_user_stats

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")

MODE_LABELS = {"exp": "TeraBox", "exphd": "TeraBox HD", "dw": "Diskwala"}


def _mode_label(mode: str | None) -> str:
    return MODE_LABELS.get(mode or "", mode or "—")


def build_stats_text(
    user_stats: dict, history_count: int, mode: str | None,
    global_stats: dict | None = None,
) -> str:
    lines = [
        "**📊 Your Stats**\n",
        f"**Downloads:** {user_stats.get('dl_count', 0)}",
        f"**Data fetched:** {format_size(user_stats.get('dl_bytes', 0))}",
        f"**Recent (kept):** {history_count} items — /history",
        f"**Default mode:** {_mode_label(mode)}",
    ]

    if global_stats:
        t = global_stats.get("totals", {})
        d = global_stats.get("today", {})
        lines += [
            "\n**🌍 Global**",
            f"Today: {d.get('ok', 0)} ✓ · {d.get('fail', 0)} ✗",
            f"All time: {t.get('ok', 0)} ✓ · {format_size(t.get('bytes', 0))}",
        ]

    return "\n".join(lines)


@bot.on(events.NewMessage(pattern=r"^/stats$"))
async def cmd_stats(event):
    chat_id = event.chat_id

    user_stats = await asyncio.to_thread(get_user_stats, chat_id)
    history = await asyncio.to_thread(get_history, chat_id)

    gs = None
    if ADMIN_ID and chat_id == ADMIN_ID:
        try:
            gs = await asyncio.to_thread(get_stats)
        except Exception as e:
            log.warning("stats fetch failed", extra={"error": str(e)})

    try:
        from firebase_db.users import get_user_mode
        mode = await asyncio.to_thread(get_user_mode, chat_id)
    except Exception:
        mode = None

    await event.respond(build_stats_text(user_stats, len(history), mode, gs))
