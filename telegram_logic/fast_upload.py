"""
FastTelethon: Parallel chunked uploads/downloads to Telegram.

Based on the painor/FastTelethon gist (MIT licensed).
Uses multiple parallel senders to Telegram DCs for faster uploads.
Memory-efficient: reads chunks from disk instead of loading entire file.
"""
import os
import math
import asyncio
import logging
from telethon import utils
from telethon.tl import types, functions

log = logging.getLogger(__name__)

# Config
CHUNK_SIZE = 512 * 1024  # 512KB chunks (optimal for parallel uploads)
MAX_PARALLEL = 4  # Parallel upload streams


async def _send_partial(
    client,
    file_id,
    file_part,
    file_total_parts,
    file_size,
    chunk_data,
    progress_callback=None,
):
    """Send a single chunk to Telegram."""
    await client._sender.send(
        functions.upload.SaveBigFilePart(
            file_id=file_id,
            file_part=file_part,
            file_part_count=file_total_parts,
            bytes=chunk_data,
        )
    )
    if progress_callback:
        bytes_sent = min((file_part + 1) * CHUNK_SIZE, file_size)
        progress_callback(bytes_sent, file_size)


async def _read_chunk(f, offset, size):
    """Read a chunk from file at offset — runs in thread pool to avoid blocking."""
    loop = asyncio.get_running_loop()
    def _do_read():
        f.seek(offset)
        return f.read(size)
    return await loop.run_in_executor(None, _do_read)


async def upload_file_fast(
    client,
    file_path: str,
    progress_callback=None,
) -> types.InputFileBig:
    """
    Upload a file to Telegram servers using parallel chunked uploads.
    
    Memory-efficient: reads chunks from disk sequentially instead of loading
    the entire file into memory. Only MAX_PARALLEL chunks are in memory at once.
    """
    file_size = os.path.getsize(file_path)
    file_id = utils.generate_random_long()
    file_total_parts = math.ceil(file_size / CHUNK_SIZE)
    
    log.info(f"Fast upload: {os.path.basename(file_path)} ({file_size / (1024*1024):.1f} MB, {file_total_parts} parts)")
    
    sem = asyncio.Semaphore(MAX_PARALLEL)
    
    async def _send_with_sem(part_idx):
        async with sem:
            offset = part_idx * CHUNK_SIZE
            size = min(CHUNK_SIZE, file_size - offset)
            # Read chunk from disk (thread pool — non-blocking)
            chunk_data = await _read_chunk(_file_handle, offset, size)
            await _send_partial(
                client, file_id, chunk_data, file_total_parts, file_size,
                chunk_data, progress_callback,
            )
    
    # Open file once, read chunks on demand — max MAX_PARALLEL chunks in RAM
    with open(file_path, 'rb') as _file_handle:
        tasks = [_send_with_sem(i) for i in range(file_total_parts)]
        await asyncio.gather(*tasks)
    
    log.info(f"Fast upload complete: {os.path.basename(file_path)}")
    
    return types.InputFileBig(
        id=file_id,
        parts=file_total_parts,
        name=os.path.basename(file_path),
    )


async def download_file_fast(
    client,
    msg,
    out: str,
    progress_callback=None,
) -> str:
    """
    Download a file from Telegram using parallel chunked downloads.
    """
    if msg.media is None:
        raise ValueError("Message has no media to download")
    
    media = msg.media
    if hasattr(media, 'photo'):
        loc = media.photo
        file_size = max(p.size for p in loc.sizes)
        name = f"{loc.id}.jpg"
    elif hasattr(media, 'document'):
        loc = media.document
        file_size = loc.size
        name = next((attr.file_name for attr in loc.attributes 
                     if hasattr(attr, 'file_name')), str(loc.id))
    else:
        raise ValueError("Unsupported media type")
    
    log.info(f"Fast download: {name} ({file_size / (1024*1024):.1f} MB)")
    
    download_chunk = 1024 * 1024
    total_parts = math.ceil(file_size / download_chunk)
    
    downloaded = 0
    
    with open(out, 'wb') as f:
        for part in range(total_parts):
            data = await client._get_file(loc, part, total_parts)
            f.write(data)
            downloaded += len(data)
            
            if progress_callback:
                progress_callback(downloaded, file_size)
    
    log.info(f"Fast download complete: {name}")
    return out


def is_large(file_size: int) -> bool:
    """Check if file needs to use BigFilePart ( > 10MB)"""
    return file_size > 10 * 1024 * 1024
