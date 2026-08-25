"""
Inline mode — trigger downloads from ANY chat the bot can see.

Flow:
  1. User types `@botusername <link>` anywhere on Telegram.
  2. Bot offers a "Download this video" inline result.
  3. Selecting it posts the link as the user's own message in that chat;
     the bot's normal message pipeline then processes it like any pasted
     link (PM and group chats with the bot present).

Requires "Inline mode" enabled via @BotFather (one-time toggle).
"""
from telethon import events

from ..bot import bot
from ..helpers import extract_all_terabox_url_exp, extract_surl_exp
from ..structured_log import ctx_logger

log = ctx_logger(__name__)


def first_url(text: str) -> str | None:
    urls = extract_all_terabox_url_exp(text or "")
    return urls[0] if urls else None


@bot.on(events.InlineQuery)
async def inline_handler(event):
    builder = event.builder
    url = first_url(event.text)

    if url:
        try:
            surl = extract_surl_exp(url)
            title = f"📥 Download {surl}" if surl else "📥 Download this video"
        except Exception:
            title = "📥 Download this video"
        # The posted message is just the link itself — handle_message does the rest.
        results = [builder.article(
            title=title,
            description="Post this link to start the download",
            text=url,
        )]
    else:
        results = [builder.article(
            title="📥 TeraBox video downloader",
            description="Paste a share-link after my username",
            text=(
                "**TeraBox Downloader**\n\n"
                "Type `@bot <link>` with a TeraBox share link "
                "and select the result to download it here."
            ),
        )]

    try:
        await event.answer(results, cache_time=5)
    except Exception as e:
        log.warning("inline answer failed", extra={"error": str(e)})
