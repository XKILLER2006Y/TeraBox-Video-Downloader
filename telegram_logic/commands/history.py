"""
/history — show the user's recent downloads (last 20, newest first).
Data is recorded on every successful /exp, /dw and /dl delivery.
"""
import time
import asyncio
from telethon import events

from ..bot import bot
from ..structured_log import ctx_logger

log = ctx_logger(__name__)


@bot.on(events.NewMessage(pattern=r"^/history$"))
async def cmd_history(event):
    log.info(f"Received /history command from chat {event.chat_id}")

    from firebase_db.users import get_history
    try:
        history = await asyncio.to_thread(get_history, event.chat_id)
    except Exception as e:
        log.error(f"/history DB error: {e}")
        await event.respond("⚠️ Database error — try again later.")
        raise events.StopPropagation

    if not history:
        await event.respond(
            "📭 No downloads yet.\n\nSend me a TeraBox or Diskwala link to get started!"
        )
        raise events.StopPropagation

    lines = ["🕘 **Your recent downloads**", ""]
    for entry in reversed(history[-10:]):  # newest first, cap display at 10
        title = entry.get("t", "unknown")
        when = time.strftime("%d %b %H:%M", time.localtime(entry.get("at", 0)))
        lines.append(f"• `{title}` — {when}")

    extra = len(history) - 10
    if extra > 0:
        lines.append(f"\n_…and {extra} older (keeping last 20)_")

    await event.respond("\n".join(lines))
    raise events.StopPropagation
