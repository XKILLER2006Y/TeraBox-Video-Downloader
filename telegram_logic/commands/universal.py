"""
/dl command — explicit universal DL for any supported platform.

Usage: /dl <url>
Supports: filesadda, GoFile, StreamTape, Doodstream, MixDrop, StreamWish,
           FileLions, CatBox, MediaFire
"""
import logging
from telethon import events
from telegram_logic.bot import bot

logger = logging.getLogger(__name__)


@bot.on(events.NewMessage(pattern=r"^/dl(?:@\S+)?(?:\s+(.+))?$"))
async def handle_dl(event):
    """Handle /dl <url> — universal download command."""
    from telegram_logic.universal import process_universal

    url = event.pattern_match.group(1).strip()
    if not url:
        await event.respond("Usage: `/dl <url>`")
        return

    logger.info(f"/dl command: {url}")
    await process_universal(event, url, bot)
