import asyncio
import logging
from telethon import events
from ..bot import bot
from ..helpers import extract_all_terabox_url_exp
from ..terabox_exp import process_terabox_experimental
from diskwalaDL.public_api import extract_all_diskwala_urls

_DISKWALA_HINT = (
    "🔗 That looks like a **Diskwala** link. Use **/dw** instead:\n`/dw <link>`\n\n"
    "…or switch your default mode to **dw** from /settings."
)

log = logging.getLogger(__name__)

# Use the broader experimental matcher (many mirror domains + flexible paths).
extract_all_terabox_urls = extract_all_terabox_url_exp

@bot.on(events.NewMessage(pattern=r"^/exp(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_get_exp(event):
    log.info(f"Received /exp command from chat {event.chat_id}")

    arg = (event.pattern_match.group(1) or "").strip()

    terabox_url_list = extract_all_terabox_urls(arg) if arg else []

    if not terabox_url_list:
        if extract_all_diskwala_urls(arg):
            await event.respond(_DISKWALA_HINT)
        else:
            await event.respond(
                "Usage: `/exp <TeraBox URL>`\n\nExample:\n`/exp https://1024tera.com/s/1XXXX`"
            )
        raise events.StopPropagation

    await asyncio.gather(*[process_terabox_experimental(event, terabox_url) for terabox_url in terabox_url_list])
    
    raise events.StopPropagation


@bot.on(events.NewMessage(pattern=r"(?i)^/exphd(?:@\S+)?(?:\s+([\s\S]+))?$"))
async def cmd_get_exp_hd(event):
    arg = (event.pattern_match.group(1) or "").strip()

    terabox_url_list = extract_all_terabox_urls(arg) if arg else []

    if not terabox_url_list:
        if extract_all_diskwala_urls(arg):
            await event.respond(_DISKWALA_HINT)
        else:
            await event.respond(
                "Usage: `/exphd <TeraBox URL>`\n\nExample:\n`/exphd https://1024tera.com/s/1XXXX`"
            )
        raise events.StopPropagation

    await asyncio.gather(*[process_terabox_experimental(event, terabox_url, is_hd=True) for terabox_url in terabox_url_list])

    raise events.StopPropagation