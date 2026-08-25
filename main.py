from dotenv import load_dotenv
load_dotenv()  # must be first — other modules read env vars at import time

import gc  # noqa: E402
import os  # noqa: E402
import asyncio  # noqa: E402
import glob  # noqa: E402
import time  # noqa: E402
import resource  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from telegram_logic.structured_log import setup_logging, ctx_logger  # noqa: E402

# ── CPython memory tuning (Cloud Shell: 2GB RAM) ─────────────────────────────────────────———————
# Reduce GC frequency: gen0 threshold 700→1500, gen1→15, gen2→10
# Fewer GC pauses = better throughput during bursty download/upload cycles.
gc.set_threshold(1500, 15, 10)

# Tell glibc to return freed pages to the OS aggressively (Linux only).
# Without this, glibc holds onto freed memory, inflating RSS.
os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "65536")
os.environ.setdefault("MALLOC_MMAP_THRESHOLD_", "65536")

from fastapi import FastAPI  # noqa: E402
import uvicorn  # noqa: E402
from telethon import events  # noqa: E402
from telethon.tl.functions.bots import SetBotCommandsRequest  # noqa: E402
from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer  # noqa: E402
import telegram_logic.bot as telegram_logic_bot  # noqa: E402
from telegram_logic.bot import bot  # noqa: E402
from telegram_logic.terabox_exp import process_terabox_experimental  # noqa: E402
from telegram_logic.diskwala import process_diskwala  # noqa: E402
from telegram_logic.helpers import extract_all_terabox_url_exp, env_int, cap_links  # noqa: E402
from diskwalaDL.public_api import extract_all_diskwala_urls  # noqa: E402
from universalDL import extract_universal_urls  # noqa: E402
from telegram_logic.universal import process_universal  # noqa: E402
from telegram_logic import alerts as _alerts  # noqa: E402
from teraboxDL.terabox_dl import set_pool_exhausted_hook  # noqa: E402
from firebase_db.users import track_user, get_user_mode  # noqa: E402

# — Global User Tracker ———————————————————————————————————————————————————————————————————————————————————

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
    except Exception:
        log.error("track_user failed", extra={"chat_id": event.chat_id}, exc_info=True)
    # Does not raise StopPropagation, allowing other handlers to execute

import telegram_logic.commands  # registers all @bot.on(...) handlers  # noqa: E402, F401

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
APP_ID = env_int("APP_ID")
API_HASH = os.environ.get("API_HASH", "")
STORAGE_GROUP_ID = env_int("STORAGE_GROUP_ID")

setup_logging()
log = ctx_logger(__name__)

# — Wrong-source hints ————————————————————————————————————————————————————————————————————————
DISKWALA_IN_TERABOX_MODE = (
    "🔗 That looks like a **Diskwala** link, but your current mode downloads **TeraBox** videos.\n\n"
    "➡️ Use the **/dw** command:\n`/dw <link>`\n\n"
    "…or switch your default mode to **dw** from /settings."
)

TERABOX_IN_DISKWALA_MODE = (
    "🔗 That looks like a **TeraBox** link, but your current mode is **dw** (Diskwala).\n\n"
    "➡️ Use **/exp** or **/exphd**:\n`/exp <link>`\n\n"
    "…or switch your default mode from /settings."
)


async def _run_batch(event, urls: list[str], processor) -> None:
    """
    Process a batch of links sequentially (gentle on Telegram and upstream),
    with a per-message cap to prevent abuse.
    """
    urls, dropped = cap_links(urls)
    if dropped > 0:
        await event.respond(
            f"⚠️ Too many links — processing the first {len(urls)}. "
            f"Send the rest in a follow-up message."
        )
    for url in urls:
        await processor(event, url)


# — Basic Message Handler ————————————————————————————————————————————————————————————————————————————

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
    except Exception:
        log.error("DB error fetching user mode", extra={"chat_id": event.chat_id}, exc_info=True)
        await event.respond("⚠️ Database error. Please try again later.")
        return

    if mode == 'exp':
        terabox_url_list = extract_all_terabox_url_exp(text)
        if terabox_url_list:
            try:
                log.info("routed to exp mode", extra={"chat_id": event.chat_id, "url_count": len(terabox_url_list)})
                await _run_batch(event, terabox_url_list, process_terabox_experimental)
            except Exception:
                log.error("unhandled error in handle_message", extra={"chat_id": event.chat_id}, exc_info=True)
            return

    elif mode == 'exphd':
        terabox_url_list = extract_all_terabox_url_exp(text)
        if terabox_url_list:
            try:
                log.info("routed to exphd mode", extra={"chat_id": event.chat_id, "url_count": len(terabox_url_list)})
                await _run_batch(
                    event, terabox_url_list,
                    lambda ev, url: process_terabox_experimental(ev, url, is_hd=True),
                )
            except Exception:
                log.error("unhandled error in handle_message", extra={"chat_id": event.chat_id}, exc_info=True)
            return

    elif mode == 'dw':
        diskwala_url_list = extract_all_diskwala_urls(text)
        if diskwala_url_list:
            try:
                log.info("routed to dw mode", extra={"chat_id": event.chat_id, "url_count": len(diskwala_url_list)})
                await _run_batch(event, diskwala_url_list, process_diskwala)
            except Exception:
                log.error("unhandled error in handle_message", extra={"chat_id": event.chat_id}, exc_info=True)
            return

    # ── Universal DL fallback — try all platforms if no mode-specific match ─
    universal_url_list = extract_universal_urls(text)
    if universal_url_list:
        try:
            log.info("universal DL: processing links", extra={"chat_id": event.chat_id, "url_count": len(universal_url_list)})
            await _run_batch(event, universal_url_list, lambda ev, url: process_universal(ev, url, bot))
        except Exception:
            log.error("unhandled error in universal DL", extra={"chat_id": event.chat_id}, exc_info=True)

    return
# — Telegram bot runner ———————————————————————————————————————————————————————————————————————

async def run_bot() -> None:
    if not BOT_TOKEN or not APP_ID or not API_HASH:
        log.error("ERROR: Set BOT_TOKEN, APP_ID, and API_HASH in your .env file!")
        return

    if not STORAGE_GROUP_ID:
        log.warning("STORAGE_GROUP_ID not set — caching disabled, videos will be sent directly.")

    # ── Threadpool: configurable via THREADPOOL_SIZE env (default 8 for low-RAM VPS) ──
    loop = asyncio.get_running_loop()
    tp_size = env_int("THREADPOOL_SIZE", 8)
    loop.set_default_executor(ThreadPoolExecutor(max_workers=tp_size, thread_name_prefix="bot"))

    # ── Pre-warm HTTP connections to eliminate first-request latency ─────────
    from network import prewarm_connections
    await asyncio.to_thread(prewarm_connections)

    # Alerting needs the running loop; cookie-pool exhaustion DMs the admin.
    _alerts.set_alert_loop(asyncio.get_running_loop())
    set_pool_exhausted_hook(
        lambda: _alerts.dispatch(
            "🍪 TeraBox cookie pool exhausted — every configured cookie is "
            "rate-limited or expired. Update COOKIES1..N in .env.",
            key="cookie-pool",
        )
    )

    await bot.start(bot_token=BOT_TOKEN)

    # Validate the storage group is reachable — a fresh bot session cannot
    # resolve peers it has never seen, which silently disables caching and
    # wastes a full pre-upload on every request before failing over.
    if STORAGE_GROUP_ID:
        try:
            await bot.get_input_entity(STORAGE_GROUP_ID)
            log.info("storage group reachable", extra={"storage_group_id": STORAGE_GROUP_ID})
        except Exception as e:
            log.error(
                "storage group not accessible — continuing without caching",
                extra={"storage_group_id": STORAGE_GROUP_ID, "error": str(e)},
            )

    default_commands = [ 
        BotCommand(command="start", description="Start BOT"),
        BotCommand(command="exp", description="[Experimental] Download TeraBox video (add 720p/1080p for quality)"), 
        BotCommand(command="exphd", description="[Experimental] Download HD TeraBox video"), 
        BotCommand(command="dw", description="Download Diskwala video"),
        BotCommand(command="dl", description="Download from any supported host (GoFile, StreamTape, Dood, MediaFire, etc.)"),
        BotCommand(command="random", description="Get a random video"),
        BotCommand(command="status", description="Bot health & stats"),
        BotCommand(command="history", description="Your recent downloads"),
        BotCommand(command="stats", description="Your download statistics"),
        BotCommand(command="mp3", description="Extract audio from a TeraBox video"),
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

    log.info("bot started, waiting for messages")

    await bot.run_until_disconnected()


# — FastAPI app ———————————————————————————————————————————————————————————————————————————————————————————————————————

# — Storage cleanup for Railway (ephemeral storage) ——————————————————————————————————————

async def _storage_cleanup_loop():
    """Periodically clean old files from storage to prevent disk exhaustion."""
    storage_dir = os.path.join(os.path.dirname(__file__), "storage")
    if not os.path.exists(storage_dir):
        os.makedirs(storage_dir, exist_ok=True)
    
    while True:
        try:
            await asyncio.sleep(120)  # Every 2 minutes (Cloud Shell: 5GB disk)
            now = time.time()
            cleaned = 0
            for f in glob.glob(os.path.join(storage_dir, "*")):
                if os.path.isfile(f):
                    age = now - os.path.getmtime(f)
                    if age > 600:  # 10 minutes
                        os.remove(f)
                        cleaned += 1
            if cleaned > 0:
                log.info("storage cleanup done", extra={"files_removed": cleaned})
        except Exception as e:
            log.error(f"[Storage Cleanup] Error: {e}")


async def _memory_monitor_loop():
    """Log memory usage and trigger GC every 5 minutes for Cloud Shell visibility."""
    while True:
        try:
            await asyncio.sleep(300)
            kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            log.info("memory snapshot", extra={"peak_rss_mb": kb // 1024})
            # Manual GC sweep + compact after logging — reclaims fragmented memory
            log.debug("gc sweep", extra={"collected": gc.collect(generation=2)})
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(run_bot())
    cleanup_task = asyncio.create_task(_storage_cleanup_loop())
    mem_task = asyncio.create_task(_memory_monitor_loop())
    yield
    # ── Graceful shutdown: refuse new work, let in-flight downloads finish ──
    log.info("shutdown initiated — draining active tasks")
    telegram_logic_bot.shutting_down.set()
    leftover = await telegram_logic_bot.drain_active_tasks(timeout=90.0)
    if leftover:
        log.warning("shutdown: tasks did not finish in time", extra={"leftover": leftover})
    mem_task.cancel()
    cleanup_task.cancel()
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
