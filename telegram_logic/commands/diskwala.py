import logging
from telethon import events
from ..bot import bot
from ..diskwala import process_diskwala
from ..helpers import extract_all_terabox_url_exp, cap_links
from diskwalaDL.public_api import extract_all_diskwala_urls

log = logging.getLogger(__name__)


@bot.on(events.NewMessage(pattern=r"(?i)^/dw(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_dw(event):
    log.info(f"Received /dw command from chat {event.chat_id}")

    arg = (event.pattern_match.group(1) or "").strip()

    diskwala_url_list = extract_all_diskwala_urls(arg) if arg else []

    if not diskwala_url_list:
        if extract_all_terabox_url_exp(arg):
            await event.respond(
                "🔗 That looks like a **TeraBox** link. Use **/exp** or **/exphd**:\n"
                "`/exp <link>`\n\n"
                "…or switch your default mode from /settings."
            )
        else:
            await event.respond(
                "Usage: `/dw <Diskwala URL>`\n\n"
                "Example:\n`/dw https://www.diskwala.com/app/6a610df006ba7ea03d7ad63d`"
            )
        raise events.StopPropagation

    urls, dropped = cap_links(diskwala_url_list)
    if dropped > 0:
        await event.respond(
            f"⚠️ Too many links — processing the first {len(urls)}. "
            f"Send the rest in a follow-up message."
        )
    for url in urls:
        await process_diskwala(event, url)

    raise events.StopPropagation
