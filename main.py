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
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
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
from firebase_db.users import track_user  # noqa: E402

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

    # ── Auto-detect platform and route (no mode check needed) ────────────────
    terabox_url_list = extract_all_terabox_url_exp(text)
    if terabox_url_list:
        try:
            log.info("auto-detect [terabox]", extra={"chat_id": event.chat_id, "url_count": len(terabox_url_list)})
            await _run_batch(event, terabox_url_list, process_terabox_experimental)
        except Exception:
            log.error("unhandled error in handle_message", extra={"chat_id": event.chat_id}, exc_info=True)
        return

    diskwala_url_list = extract_all_diskwala_urls(text)
    if diskwala_url_list:
        try:
            log.info("auto-detect [diskwala]", extra={"chat_id": event.chat_id, "url_count": len(diskwala_url_list)})
            await _run_batch(event, diskwala_url_list, process_diskwala)
        except Exception:
            log.error("unhandled error in handle_message", extra={"chat_id": event.chat_id}, exc_info=True)
        return

    universal_url_list = extract_universal_urls(text)
    if universal_url_list:
        try:
            log.info("auto-detect [universal]", extra={"chat_id": event.chat_id, "url_count": len(universal_url_list)})
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

    # Start flood-queue worker before any traffic so it never inherits
    # the first request's logging context.
    await telegram_logic_bot.terabox_queue.ensure_worker()

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
        BotCommand(command="dl", description="Download from any platform (TeraBox, Diskwala, GoFile, etc.)"),
        BotCommand(command="random", description="Get a random video"),
        BotCommand(command="status", description="Bot health & stats"),
        BotCommand(command="history", description="Your recent downloads"),
        BotCommand(command="stats", description="Your download statistics"),
        BotCommand(command="quota", description="Your remaining downloads today"),
        BotCommand(command="mp3", description="Extract audio from a video"),
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
            telegram_logic_bot.last_heartbeat = time.time()
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
            telegram_logic_bot.last_heartbeat = time.time()
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


# — Deep healthcheck ————————————————————————————————————————————————————————————————

@app.get("/health")
async def health():
    """Container-grade health: 200 only when the bot is connected and loops tick."""
    connected = bot.is_connected()
    hb_age = time.time() - telegram_logic_bot.last_heartbeat
    healthy = connected and hb_age < 600
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "connected": connected,
            "heartbeat_age_s": round(hb_age, 1),
            "uptime_s": round(time.time() - telegram_logic_bot.START_TIME),
        },
    )


# — Live dashboard (opt-in via DASHBOARD_TOKEN) —————————————————————————————————————

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

_DASH_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Bot Dashboard</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font-family:ui-monospace,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}
h1{font-size:18px;color:#58a6ff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.card .v{font-size:26px;font-weight:700;color:#3fb950}.card .l{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#8b949e}
.warn .v{color:#d29922}table{width:100%;border-collapse:collapse;font-size:13px}
td,th{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d}th{color:#8b949e}
.ok{color:#3fb950}.bad{color:#f85149}.refreshed{font-size:11px;color:#8b949e}
</style></head><body>
<h1>🤖 TeraBox Bot — Live</h1>
<div class=refreshed id=ref>connecting…</div>
<div class=grid id=cards></div>
<h1 style=font-size:14px>Cookie pool</h1><table id=t></table>
<script>
const tok=new URLSearchParams(location.search).get('t')||'';
async function tick(){
 try{
  const r=await fetch('/api/stats?t='+tok);if(!r.ok)throw 0;
  const d=await r.json();
  document.getElementById('ref').textContent='updated '+new Date().toLocaleTimeString();
  const c=(l,v,warn)=>`<div class="card ${warn?'warn':''}"><div class=v>${v}</div><div class=l>${l}</div></div>`;
  document.getElementById('cards').innerHTML=
    c('uptime',d.uptime)+c('active downloads',d.active,d.active>=5)+
    c('queue',d.queue,d.queue>5)+c('memory (rss)',d.mem)+
    c('today ✓',d.today_ok)+c('today ✗',d.today_fail,d.today_fail>d.today_ok);
  document.getElementById('t').innerHTML='<tr><th>cookie</th><th>state</th></tr>'+
    d.cookies.map(k=>`<tr><td>#${k.index}</td><td class="${k.state=='ok'?'ok':(k.state=='bad'?'bad':'')}">${k.state}</td></tr>`).join('')
    ||'<tr><td colspan=2>no cookies configured</td></tr>';
 }catch(e){document.getElementById('ref').textContent='fetch failed — check DASHBOARD_TOKEN';}
}
tick();setInterval(tick,3000);
</script></body></html>"""


@app.get("/dash", response_class=HTMLResponse)
async def dash(t: str = ""):
    if not DASHBOARD_TOKEN:
        return HTMLResponse("<h1>Dashboard disabled</h1><p>Set DASHBOARD_TOKEN in .env to enable.</p>", status_code=404)
    if t != DASHBOARD_TOKEN:
        return HTMLResponse("<h1>403</h1>", status_code=403)
    return HTMLResponse(_DASH_HTML)


@app.get("/api/stats")
async def api_stats(t: str = ""):
    if not DASHBOARD_TOKEN or t != DASHBOARD_TOKEN:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    from firebase_db.stats import get_stats as _gs

    def _fmt_uptime(sec: float) -> str:
        d, rem = divmod(int(sec), 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        return f"{d}d {h}h {m}m" if d else (f"{h}h {m}m" if h else f"{m}m")

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    gs = await asyncio.to_thread(_gs)

    from teraboxDL.terabox_dl import cookie_pool_health

    return {
        "uptime": _fmt_uptime(time.time() - telegram_logic_bot.START_TIME),
        "active": len(active_tasks_ref()),
        "queue": terabox_queue_ref(),
        "mem": f"{int(kb // 1024)}MB",
        "today_ok": gs["today"]["ok"],
        "today_fail": gs["today"]["fail"],
        "cookies": await asyncio.to_thread(cookie_pool_health),
    }


def active_tasks_ref():
    return telegram_logic_bot.active_tasks


def terabox_queue_ref() -> int:
    return telegram_logic_bot.terabox_queue.pending

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=env_int("PORT", 3000))
