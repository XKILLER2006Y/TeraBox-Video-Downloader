import asyncio
import random
from telethon import events
from ..bot import bot
from firebase_db.cache import get_cache_for_random
from ..helpers import env_int
from ..structured_log import ctx_logger
STORAGE_GROUP_ID = env_int("STORAGE_GROUP_ID")

log = ctx_logger(__name__)

@bot.on(events.NewMessage(pattern=r"^/random$"))
async def cmd_random(event):
    log.info(f"Received /random command from chat {event.chat_id}")

    try:
        data = await asyncio.to_thread(get_cache_for_random)
    except Exception as e:
        log.error(f"[/random] DB error fetching cache: {e}")
        await event.respond(
            "⚠️ **Database error** — could not fetch the video cache.\n"
            "Please try again in a moment."
        )
        raise events.StopPropagation

    if not data:
        await event.respond("📭 No videos yet. Send a TeraBox link first!")
        raise events.StopPropagation

    # Try up to 3 distinct entries — deleted storage messages otherwise act
    # as permanent tombstones (there is no cache-delete API to prune them).
    entries = list(data.items())
    random.shuffle(entries)
    cached_msg = None
    for surl, msg_id in entries[:3]:
        if not STORAGE_GROUP_ID:
            break
        try:
            m = await bot.get_messages(STORAGE_GROUP_ID, ids=msg_id)
        except Exception as e:
            log.warning(f"Random fetch failed for surl={surl} msg_id={msg_id}: {e}")
            continue
        if m and (m.video or (
            m.document and m.document.mime_type and "video" in m.document.mime_type
        )):
            cached_msg = m
            break
        log.warning(f"Tombstoned cache entry skipped: {surl}")

    if cached_msg is None:
        await event.respond("⚠️ Could not retrieve random video. Try again!")
        raise events.StopPropagation

    f = cached_msg.file
    fname = (f.name if f and f.name else surl)
    caption = f"📦 `{fname}`"
    await bot.send_file(
        event.chat_id, cached_msg.media,
        caption=caption, supports_streaming=True, reply_to=event.message.id,
    )
    raise events.StopPropagation

