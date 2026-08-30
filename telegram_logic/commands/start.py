from ..bot import bot
from ..structured_log import ctx_logger, bind_context, new_request_id
from ..helpers import extract_all_terabox_url_exp
from telethon import events

log = ctx_logger(__name__)

WELCOME_MESSAGE = (
    "🚀 **Welcome!**\n\n"
    "📥 **Send any link** — I'll auto-detect the platform and download it.\n\n"
    "**/dl** <link> [quality]  Download from any platform\n"
    "  Qualities: 360p, 480p, 720p, 1080p\n"
    "  Add **comp** to shrink the video\n\n"
    "Supported: TeraBox, Diskwala, filesadda, GoFile, StreamTape, "
    "Dood, MixDrop, StreamWish, FileLions, CatBox, MediaFire\n\n"
    "🎲 **/random**  Get a random video\n"
    "📊 **/stats**  Your download statistics\n"
    "🕘 **/history**  Your recent downloads\n"
    "🎤 **/mp3** <link>  Extract audio as MP3\n\n"
    "💡 Just paste a link — no command needed!\n"
    "📩 Send feedback to admin using **/op** <your message>"
)

# A link passed as a start argument ("/start https://…") — process it right away.
_URL_IN_START = r"^/start(?:@\S+)?(?:\s+(.+))?$"


@bot.on(events.NewMessage(pattern=_URL_IN_START))
async def cmd_start(event):
    arg = (event.pattern_match.group(1) or "").strip()
    log.info("received /start", extra={"chat_id": event.chat_id, "has_arg": bool(arg)})
    await event.respond(WELCOME_MESSAGE)

    if not arg:
        return

    rid = new_request_id()
    bind_context(request_id=rid, user_id=event.chat_id)
    log.info("start carried download link", extra={"arg": arg[:60]})

    from .universal import handle_dl
    # Let handle_dl parse and route whatever URL was passed to /start
    await handle_dl(event)
    raise events.StopPropagation
