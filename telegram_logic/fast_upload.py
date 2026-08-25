"""
FastTelethon: Parallel chunked uploads/downloads to Telegram.

Based on the painor/FastTelethon gist (MIT licensed).
Uses multiple parallel senders to Telegram DCs for faster uploads.
Memory-efficient: reads chunks from disk instead of loading entire file.
"""
import os
import math
import asyncio
from .structured_log import ctx_logger
from telethon import utils  # noqa: F401 — kept for future Telethon helpers
from telethon.tl import types, functions

log = ctx_logger(__name__)

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
        functions.upload.SaveBigFilePartRequest(
            file_id=file_id,
            file_part=file_part,
            file_total_parts=file_total_parts,
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
    # 63-bit random file id (telethon.utils.generate_random_long was
    # removed in newer Telethon versions)
    file_id = int.from_bytes(os.urandom(8), "big") & 0x7FFFFFFFFFFFFFFF
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
                client, file_id, part_idx, file_total_parts, file_size,
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


def is_large(file_size: int) -> bool:
    """Check if file needs to use BigFilePart ( > 10MB)"""
    return file_size > 10 * 1024 * 1024
