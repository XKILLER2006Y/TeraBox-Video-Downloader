from ..bot import bot
from ..structured_log import ctx_logger, bind_context, new_request_id
from ..helpers import extract_all_terabox_url_exp
from telethon import events

log = ctx_logger(__name__)

WELCOME_MESSAGE = (
    "🚀 **Welcome!**\n\n"
    "⚙️ **Commands:**\n"
    "**/exp** <link>  Reliable & Fast [Recommended]\n"
    "**/expHD** <link>  For HD Videos [Slow]\n"
    "**/dw** <link>  Download Diskwala video\n"
    "**/mp3** <link>  Extract audio as MP3\n\n"
    "🎲 **/random**  Get a random video\n"
    "📊 **/stats**  Your download statistics\n"
    "🕘 **/history**  Your recent downloads\n"
    "🔧 **/settings**  Change default mode [/exp is default]\n\n"
    "📥 Give me **TeraBox link(s)** (paste or forward them), I'll send the videos.\n\n"
    "💡 You can also just send a link without any command, I'll use your default setting.\n"
    "💡 Or type `@mybot <link>` in any chat with inline mode.\n\n"
    "📩 Send feedback to admin using **/op** <your message>"
)

# A link passed as a start argument ("/start https://…") — process it right away.
_URL_IN_START = r"^/start(?:@\S+)?(?:\s+(.+))?$"


@bot.on(events.NewMessage(pattern=_URL_IN_START))
async def cmd_start(event):
    arg = (event.pattern_match.group(1) or "").strip()
    log.info("received /start", extra={"chat_id": event.chat_id, "has_arg": bool(arg)})
    await event.respond(WELCOME_MESSAGE)

    urls = extract_all_terabox_url_exp(arg)
    if not urls:
        return

    rid = new_request_id()
    bind_context(request_id=rid, user_id=event.chat_id)
    log.info("start carried download link", extra={"url_count": len(urls)})

    from ..terabox_exp import process_terabox_experimental
    for url in urls[:1]:  # single link per /start keeps first contact light
        await process_terabox_experimental(event, url)

    raise events.StopPropagation
