import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn
from telethon import events
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer
from telegram_logic.bot import bot
from telegram_logic.terabox_trad import process_terabox
from telegram_logic.terabox_exp import process_terabox_experimental
from telegram_logic.diskwala import process_diskwala
from telegram_logic.helpers import extract_all_surls, extract_all_terabox_url_exp, env_int
from diskwalaDL.public_api import extract_all_diskwala_urls
from universalDL import extract_universal_urls
from telegram_logic.universal import process_universal
from firebase_db.users import track_user, get_user_mode

# — Global User Tracker ——————————————————————————————————————————————————————————————————

@bot.on(events.NewMessage)
async def global_tracker(event):
    # Ignore the storage group — its own posts are not user activity,
    # and tracking it would pollute /recent and /broadcast targets.
    if STORAGE_GROUP_ID and event.chat_id == STORAGE_GROUP_ID:
        return

    username = None

    try:
        sender = await event.get_sender()
        if sender and getattr(sender, 'username', None):
            username = sender.username
        elif getattr(event.chat, 'username', None):
            username = event.chat.username
    except Exception:
        pass

    try:
        await asyncio.to_thread(track_user, event.chat_id, username)
    except Exception as e:
        log.error(f"[global_tracker] Unexpected error in track_user: {e}")
    # Does not raise StopPropagation, allowing other handlers to execute

import telegram_logic.commands  # registers all @bot.on(...) handlers  # noqa: E402, F401

from dotenv import load_dotenv  # noqa: E402
load_dotenv()  # noqa: E402

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
APP_ID = env_int("APP_ID")
API_HASH = os.environ.get("API_HASH", "")
STORAGE_GROUP_ID = env_int("STORAGE_GROUP_ID")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO,
)
log = logging.getLogger(__name__)

# — Wrong-source hints ————————————————————————————————————————————————————————————————————————
DISKWALA_IN_TERABOX_MODE = (
    "🔗 That looks like a **Diskwala** link, but your current mode downloads **TeraBox** videos.\n\n"
    "➡️ Use the **/dw** command:\n`/dw <link>`\n\n"
    "…or switch your default mode to **dw** from /settings."
)

TERABOX_IN_DISKWALA_MODE = (
    "🔗 That looks like a **TeraBox** link, but your current mode is **dw** (Diskwala).\n\n"
    "➡️ Use **/exp**, **/exphd** or **/get**:\n`/exp <link>`\n\n"
    "…or switch your default mode from /settings."
)

# — Basic Message Handler ————————————————————————————————————————————————————————————————————

@bot.on(events.NewMessage)
async def handle_message(event):
    # Storage group posts are never download requests
    if STORAGE_GROUP_ID and event.chat_id == STORAGE_GROUP_ID:
        return

    text = event.raw_text or ""
    if text.startswith("/"):
        return  # Let command handlers deal with commands
    
    # Get mode based on user-id..
    try:
        mode = await asyncio.to_thread(get_user_mode, event.chat_id)
    except Exception as e:
        log.error(f"[handle_message] DB error fetching user mode: {e}")
        await event.respond("⚠️ Database error. Please try again later.")
        return

    if mode == 'get':
        surls = extract_all_surls(text)
        if surls:
            try:
                log.info("Message redirected to [get] mode")
                await asyncio.gather(*[process_terabox(event, surl) for surl in surls])
            except Exception as e:
                log.error(f"Unhandled error in handle_message: {e}")
            return

    elif mode == 'exp':
        terabox_url_list = extract_all_terabox_url_exp(text)
        if terabox_url_list:
            try:
                log.info("Message redirected to [exp] mode")
                await asyncio.gather(*[process_terabox_experimental(event, surl) for surl in terabox_url_list])
            except Exception as e:
                log.error(f"Unhandled error in handle_message: {e}")
            return

    elif mode == 'exphd':
        terabox_url_list = extract_all_terabox_url_exp(text)
        if terabox_url_list:
            try:
                log.info("Message redirected to [exphd] mode")
                await asyncio.gather(*[process_terabox_experimental(event, surl, is_hd=True) for surl in terabox_url_list])
            except Exception as e:
                log.error(f"Unhandled error in handle_message: {e}")
            return

    elif mode == 'dw':
        diskwala_url_list = extract_all_diskwala_urls(text)
        if diskwala_url_list:
            try:
                log.info("Message redirected to [dw] mode")
                await asyncio.gather(*[process_diskwala(event, url) for url in diskwala_url_list])
            except Exception as e:
                log.error(f"Unhandled error in handle_message: {e}")
            return

    # ── Universal DL fallback — try all platforms if no mode-specific match ─
    universal_url_list = extract_universal_urls(text)
    if universal_url_list:
        try:
            log.info(f"Universal DL: processing {len(universal_url_list)} link(s)")
            await asyncio.gather(*[process_universal(event, url, bot) for url in universal_url_list])
        except Exception as e:
            log.error(f"Unhandled error in universal DL: {e}")

    return
# — Telegram bot runner ——————————————————————————————————————————————————————————————————————

async def run_bot() -> None:
    if not BOT_TOKEN or not APP_ID or not API_HASH:
        log.error("ERROR: Set BOT_TOKEN, APP_ID, and API_HASH in your .env file!")
        return

    if not STORAGE_GROUP_ID:
        log.warning("STORAGE_GROUP_ID not set — caching disabled, videos will be sent directly.")

    # ── Threadpool: size 100 to handle concurrent downloads × 5 threads each ──
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=100, thread_name_prefix="bot"))

    # ── Pre-warm HTTP connections to eliminate first-request latency ───────
    from network import prewarm_connections
    await asyncio.to_thread(prewarm_connections)

    await bot.start(bot_token=BOT_TOKEN)

    # Validate the storage group is reachable — a fresh bot session cannot
    # resolve peers it has never seen, which silently disables caching and
    # wastes a full pre-upload on every request before failing over.
    if STORAGE_GROUP_ID:
        try:
            await bot.get_input_entity(STORAGE_GROUP_ID)
            log.info(f"Storage group {STORAGE_GROUP_ID} reachable — caching enabled.")
        except Exception as e:
            log.error(
                f"STORAGE_GROUP_ID {STORAGE_GROUP_ID} is NOT accessible to this bot ({e}). "
                f"Add the bot as an admin/member to that group or channel, then restart. "
                f"Continuing WITHOUT caching — videos will be uploaded separately per user."
            )

    default_commands = [ 
        BotCommand(command="start", description="Start BOT"),
        BotCommand(command="exp", description="[Experimental] Download TeraBox video"), 
        BotCommand(command="exphd", description="[Experimental] Download HD TeraBox video"), 
        BotCommand(command="get", description="Download TeraBox video [Unstable]"),
        BotCommand(command="dw", description="Download Diskwala video"),
        BotCommand(command="dl", description="Download from any supported host (GoFile, StreamTape, Dood, MediaFire, etc.)"),
        BotCommand(command="random", description="Get a random video"),
        BotCommand(command="settings", description="View Details"),
        BotCommand(command="op", description="Send feedback to admin"),
    ]

    await bot(SetBotCommandsRequest( 
        scope=BotCommandScopeDefault(),
        lang_code="",
        commands=default_commands
    ))

    admin_id = env_int("ADMIN_ID")
    if admin_id:
        try:
            admin_peer = await bot.get_input_entity(admin_id)
            admin_commands_list = default_commands + [
                BotCommand(command="recent", description="[Admin] Show recent users"),
                BotCommand(command="broadcast", description="[Admin] Broadcast message"),
            ]
            await bot(SetBotCommandsRequest(
                scope=BotCommandScopePeer(peer=admin_peer),
                lang_code="",
                commands=admin_commands_list
            ))
            log.info("Admin commands registered.")
        except Exception as e:
            log.error(f"Failed to set admin commands. You may need to send a message to the bot first. Error: {e}")

    log.info("Bot commands registered.")
    log.info("Bot started! Waiting for messages...")

    await bot.run_until_disconnected()


# — FastAPI app ———————————————————————————————————————————————————————————————————————————————

@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(run_bot())
    yield
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass
    if bot.is_connected():
        await bot.disconnect()
    log.info("Bye!")

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping():
    return "pong"

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=env_int("PORT", 3000))
