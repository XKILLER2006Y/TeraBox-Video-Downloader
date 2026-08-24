import logging
from telethon import events
from ..bot import bot
from ..helpers import extract_all_terabox_url_exp, parse_quality, cap_links
from ..terabox_exp import process_terabox_experimental
from diskwalaDL.public_api import extract_all_diskwala_urls

_DISKWALA_HINT = (
    "🔗 That looks like a **Diskwala** link. Use **/dw** instead:\n`/dw <link>`\n\n"
    "…or switch your default mode to **dw** from /settings."
)

_USAGE = (
    "Usage: `/exp <TeraBox URL> [quality]`\n\n"
    "Example:\n`/exp https://1024tera.com/s/1XXXX`\n"
    "With quality:\n`/exp https://1024tera.com/s/1XXXX 720p`\n\n"
    "Qualities: 360p, 480p, 720p, 1080p (default: best available)"
)

log = logging.getLogger(__name__)


@bot.on(events.NewMessage(pattern=r"^/exp(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_get_exp(event):
    log.info(f"Received /exp command from chat {event.chat_id}")

    arg = (event.pattern_match.group(1) or "").strip()

    # Optional trailing quality token: "/exp <url> 720p"
    arg, quality = parse_quality(arg)

    terabox_url_list = extract_all_terabox_url_exp(arg) if arg else []

    if not terabox_url_list:
        if extract_all_diskwala_urls(arg):
            await event.respond(_DISKWALA_HINT)
        else:
            await event.respond(_USAGE)
        raise events.StopPropagation

    urls, dropped = cap_links(terabox_url_list)
    if dropped > 0:
        await event.respond(
            f"⚠️ Too many links — processing the first {len(urls)}. "
            f"Send the rest in a follow-up message."
        )
    for terabox_url in urls:
        await process_terabox_experimental(event, terabox_url, quality=quality)

    raise events.StopPropagation


@bot.on(events.NewMessage(pattern=r"(?i)^/exphd(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_get_exp_hd(event):
    arg = (event.pattern_match.group(1) or "").strip()

    terabox_url_list = extract_all_terabox_url_exp(arg) if arg else []

    if not terabox_url_list:
        if extract_all_diskwala_urls(arg):
            await event.respond(_DISKWALA_HINT)
        else:
            await event.respond(
                "Usage: `/exphd <TeraBox URL>`\n\nExample:\n`/exphd https://1024tera.com/s/1XXXX`"
            )
        raise events.StopPropagation

    urls, dropped = cap_links(terabox_url_list)
    if dropped > 0:
        await event.respond(
            f"⚠️ Too many links — processing the first {len(urls)}. "
            f"Send the rest in a follow-up message."
        )
    for terabox_url in urls:
        await process_terabox_experimental(event, terabox_url, is_hd=True)

    raise events.StopPropagation
