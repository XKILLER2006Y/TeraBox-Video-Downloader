from telethon import events
from ..bot import bot, active_tasks
from ..structured_log import ctx_logger


def _resolve_universal_token(token: str):
    """Return (chat_id, url) for a /dl cancel token, or None.

    NOTE: absolute import — '.universal' from commands/ would point at a
    nonexistent module and the ImportError used to be swallowed to None.
    """
    from ..universal import _ucancel_tokens
    return _ucancel_tokens.get(token)

log = ctx_logger(__name__)

@bot.on(events.CallbackQuery(pattern=rb"^(?:u)?cancel:"))
async def handle_cancel(event):
    chat_id = event.chat_id
    sender_id = event.sender_id

    # Extract the key from callback data: "cancel:<surl>" / "ucancel:<url>"
    # (ucancel payloads are full URLs — split on the FIRST colon only)
    data = event.data.decode("utf-8", errors="ignore")
    _, _, key = data.partition(":")
    surl = key or None

    log.info(
        f"Received cancel: chat_id={chat_id}, sender_id={sender_id}, "
        f"surl={surl}, active_tasks keys={list(active_tasks.keys())}"
    )

    if not surl:
        await event.answer("⚠️ Invalid cancel request.")
        return

    # Universal tokens: "ucancel:<token>" resolves via the token map and
    # may only be cancelled from the chat that started it.
    if data.startswith("ucancel:"):
        info_ = _resolve_universal_token(surl)
        if not info_ or info_[0] != chat_id:
            await event.answer("Nothing to cancel.")
            return
        cancel_event = active_tasks.get(info_)
    else:
        # Look for the exact task matching (chat_id, surl) or (sender_id, surl)
        cancel_event = active_tasks.get((chat_id, surl)) or active_tasks.get((sender_id, surl))

    if cancel_event and not cancel_event.is_set():
        cancel_event.set()
        await event.answer("🚫 Cancelling this download...")
    else:
        await event.answer("Nothing to cancel.")