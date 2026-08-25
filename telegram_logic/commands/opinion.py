from telethon import events
from ..bot import bot
from ..helpers import env_int
from .. import rate_limit
from ..structured_log import ctx_logger

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")


@bot.on(events.NewMessage(pattern=r"^/op(?:\s+([\s\S]+))?$"))
async def cmd_opinion(event):
    log.info(f"Received /op command from chat {event.chat_id}")

    message_text = (event.pattern_match.group(1) or "").strip()

    if not message_text:
        await event.respond(
            "**Usage:** `/op <your message>`\n"
            "Share your opinion, feedback, or info with the admin."
        )
        return

    # Abuse guard: share the retry budget with downloads; cap length.
    blocked = rate_limit.check_rate_limit(event.chat_id)
    if blocked:
        await event.respond(blocked)
        return
    message_text = message_text[:1000]

    if not ADMIN_ID:
        log.error("ADMIN_ID not set — cannot forward opinion.")
        await event.respond("⚠️ Admin not configured. Please try again later.")
        return

    sender = await event.get_sender()
    username = getattr(sender, "username", None)
    username_display = f"@{username}" if username else "N/A"

    # No parse_mode: user text is untrusted and must not be able to forge
    # markdown structure in the admin chat.
    forward_text = (
        f"📩 Msg from a user\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {username_display}\n"
        f"🆔 Chat ID: {event.chat_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 {message_text}"
    )

    try:
        await bot.send_message(ADMIN_ID, forward_text)
    except Exception as e:
        log.error(f"Failed to forward opinion to admin: {e}")
        await event.respond("⚠️ Something went wrong. Please try again later.")
        return

    await event.respond("✅ **Opinion submitted.** Thank you for your feedback!")
    raise events.StopPropagation
