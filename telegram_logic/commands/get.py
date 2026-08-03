import asyncio
from telethon import events
import logging
from ..bot import bot
from ..terabox_trad import process_terabox
from ..helpers import extract_all_surls
from diskwalaDL.public_api import extract_all_diskwala_urls

log = logging.getLogger(__name__)

@bot.on(events.NewMessage(pattern=r"^/get(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_get(event):
    log.info(f"Received /get command from chat {event.chat_id}")

    arg = (event.pattern_match.group(1) or "").strip()

    surls = extract_all_surls(arg) if arg else []

    if not surls:
        if extract_all_diskwala_urls(arg):
            await event.respond(
                "🔗 That looks like a **Diskwala** link. Use **/dw** instead:\n`/dw <link>`\n\n"
                "…or switch your default mode to **dw** from /settings."
            )
        else:
            await event.respond(
                "Usage: `/get <TeraBox URL>`\n\nExample:\n`/get https://1024tera.com/s/1XXXX`"
            )
        raise events.StopPropagation

    await asyncio.gather(*[process_terabox(event, surl) for surl in surls])
    
    raise events.StopPropagation