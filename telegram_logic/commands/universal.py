"""
/dl command — single entry point for ALL supported platforms.

Usage: /dl <url> [quality] [comp]
Auto-detects platform and routes to the correct pipeline:
  - TeraBox (1024tera, terasharefile, terabox, …) → exp pipeline
  - Diskwala → dw pipeline
  - filesadda, GoFile, StreamTape, Dood, MixDrop, StreamWish,
    FileLions, CatBox, MediaFire → universal pipeline
"""
import logging
from telethon import events
from telegram_logic.bot import bot
from telegram_logic.helpers import (
    parse_comp_flag, parse_quality, cap_links,
    extract_all_terabox_url_exp,
)
from diskwalaDL.public_api import extract_all_diskwala_urls

logger = logging.getLogger(__name__)

_USAGE = (
    "Usage: `/dl <link> [quality]`\n\n"
    "Examples:\n"
    "`/dl https://1024tera.com/s/1XXXX`\n"
    "`/dl https://1024tera.com/s/1XXXX 720p`\n"
    "`/dl https://1024tera.com/s/1XXXX 720p comp`\n"
    "`/dl https://www.diskwala.com/app/XXXX`\n"
    "`/dl https://filesadda.site/XXXX`\n"
    "`/dl https://gofile.io/d/XXXX`\n\n"
    "Qualities: 360p, 480p, 720p, 1080p (default: best available)\n"
    "Add **comp** to shrink the video."
)


@bot.on(events.NewMessage(pattern=r"^/dl(?:@\S+)?(?:\s+(.+))?$"))
async def handle_dl(event):
    """Handle /dl <url> — universal download command with auto-routing."""
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        await event.reply(_USAGE)
        return

    # Parse optional flags: "/dl <url> 720p comp"
    arg, compress = parse_comp_flag(arg)
    arg, quality = parse_quality(arg)

    # ── Detect platform and route ────────────────────────────────────────────
    terabox_urls = extract_all_terabox_url_exp(arg)
    diskwala_urls = extract_all_diskwala_urls(arg) if not terabox_urls else []

    if terabox_urls:
        # TeraBox → exp pipeline
        from ..terabox_exp import process_terabox_experimental
        urls, dropped = cap_links(terabox_urls)
        if dropped > 0:
            await event.respond(
                f"⚠️ Too many links — processing the first {len(urls)}. "
                f"Send the rest in a follow-up message."
            )
        logger.info(f"/dl [terabox] {len(urls)} url(s), quality={quality}, comp={compress}")
        for url in urls:
            await process_terabox_experimental(event, url, quality=quality, compress=compress)
        return

    if diskwala_urls:
        # Diskwala → dw pipeline
        from ..diskwala import process_diskwala
        urls, dropped = cap_links(diskwala_urls)
        if dropped > 0:
            await event.respond(
                f"⚠️ Too many links — processing the first {len(urls)}. "
                f"Send the rest in a follow-up message."
            )
        logger.info(f"/dl [diskwala] {len(urls)} url(s)")
        for url in urls:
            await process_diskwala(event, url)
        return

    # Universal platforms (filesadda, GoFile, StreamTape, Dood, …)
    from telegram_logic.universal import process_universal
    logger.info(f"/dl [universal] {arg[:60]}")
    await process_universal(event, arg, bot)


# ── Raw URL handler (no command prefix) ──────────────────────────────────────
# When a user pastes a link without /dl, auto-detect and route.

@bot.on(events.NewMessage(
    pattern=r"https?://(?:www\.)?(?:1024tera(?:box)?\.com|terabox\.(?:com|app|fun)|"
    r"terasharefile\.com|4funbox\.(?:com|co)|mirrobox\.com|nephobox\.com|"
    r"momerybox\.com|tibibox\.com|freeterabox\.com|diskwala\.com|"
    r"filesadda\.(?:site|club|link)|gofile\.io|streamtape\.com|"
    r"dood\.(?:watch|wf|re)|mixdrop\.(?:co|to)|streamwish\.(?:to|xyz)|"
    r"filelions\.(?:to|xyz)|files\.catbox\.me|mediafire\.com)"
))
async def handle_raw_url(event):
    """Auto-detect and download when user pastes a raw URL (no /dl prefix)."""
    text = event.message.text or ""
    terabox_urls = extract_all_terabox_url_exp(text)

    if terabox_urls:
        from ..terabox_exp import process_terabox_experimental
        urls, _ = cap_links(terabox_urls)
        logger.info(f"auto-detect [terabox] {len(urls)} url(s)")
        for url in urls[:1]:  # one at a time for raw pastes
            await process_terabox_experimental(event, url)
        return

    diskwala_urls = extract_all_diskwala_urls(text)
    if diskwala_urls:
        from ..diskwala import process_diskwala
        logger.info(f"auto-detect [diskwala] {len(diskwala_urls)} url(s)")
        await process_diskwala(event, diskwala_urls[0])
        return

    # For other platforms, don't auto-trigger — let user use /dl explicitly
    # (to avoid false positives on random URLs in conversations)
