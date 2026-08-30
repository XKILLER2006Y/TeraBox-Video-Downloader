"""
FastTelethon: Parallel chunked uploads/downloads to Telegram.

Based on the painor/FastTelethon gist (MIT licensed).
Uses multiple parallel senders to Telegram DCs for faster uploads.
Memory-efficient: reads chunks from disk via pread instead of loading entire file.
"""
import os
import math
import asyncio
import threading
from .structured_log import ctx_logger
from telethon import utils  # noqa: F401 — kept for future Telethon helpers
from telethon.tl import types, functions

log = ctx_logger(__name__)

# Config
CHUNK_SIZE = 512 * 1024  # 512KB chunks (optimal for parallel uploads)
DEFAULT_MAX_PARALLEL = 8  # Parallel upload streams


async def _send_part_with_retry(client, file_id, part_idx, file_total_parts, chunk_data, max_retries=3):
    """Send a single chunk to Telegram with retries on transient errors."""
    for attempt in range(max_retries):
        try:
            await client._sender.send(
                functions.upload.SaveBigFilePartRequest(
                    file_id=file_id,
                    file_part=part_idx,
                    file_total_parts=file_total_parts,
                    bytes=chunk_data,
                )
            )
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            log.warning(f"Chunk {part_idx} attempt {attempt + 1} failed: {e}, retrying in 1s...")
            await asyncio.sleep(1 + attempt)


async def _read_chunk(f, offset, size):
    """
    Read a chunk at an absolute offset. Uses os.pread — a single atomic
    syscall that never mutates the shared file position.
    """
    loop = asyncio.get_running_loop()
    def _do_read():
        return os.pread(f.fileno(), size, offset)
    return await loop.run_in_executor(None, _do_read)


async def upload_file_fast(
    client,
    file_path: str,
    progress_callback=None,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    cancel_event: threading.Event | None = None,
) -> types.InputFileBig:
    """
    Upload a file to Telegram servers using parallel chunked uploads.
    
    Memory-efficient: reads chunks from disk on demand.
    Bounded worker queue ensures no task explosion on multi-GB files.
    Strictly monotonic progress reporting.
    """
    file_size = os.path.getsize(file_path)
    if file_size == 0:
        file_total_parts = 1
    else:
        file_total_parts = math.ceil(file_size / CHUNK_SIZE)

    file_id = int.from_bytes(os.urandom(8), "big") & 0x7FFFFFFFFFFFFFFF
    
    log.info(f"Fast upload: {os.path.basename(file_path)} ({file_size / (1024*1024):.1f} MB, {file_total_parts} parts, workers={max_parallel})")
    
    # Producer-consumer queue to bound concurrency without creating thousands of tasks
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=max_parallel * 2)
    bytes_uploaded = 0
    progress_lock = asyncio.Lock()

    async def _worker(handle):
        nonlocal bytes_uploaded
        while True:
            part_idx = await queue.get()
            if part_idx is None:
                queue.task_done()
                break

            if cancel_event and cancel_event.is_set():
                queue.task_done()
                raise asyncio.CancelledError("Upload cancelled")

            offset = part_idx * CHUNK_SIZE
            size = min(CHUNK_SIZE, file_size - offset)
            chunk_data = await _read_chunk(handle, offset, size)

            await _send_part_with_retry(client, file_id, part_idx, file_total_parts, chunk_data)

            async with progress_lock:
                bytes_uploaded += len(chunk_data)
                current_total = min(bytes_uploaded, file_size)

            if progress_callback:
                progress_callback(current_total, file_size)

            queue.task_done()

    with open(file_path, "rb") as handle:
        # Spawn fixed worker pool
        num_workers = min(max_parallel, file_total_parts)
        workers = [asyncio.create_task(_worker(handle)) for _ in range(num_workers)]

        # Enqueue parts
        try:
            for part_idx in range(file_total_parts):
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError("Upload cancelled")
                await queue.put(part_idx)

            # Wait for all parts to be processed
            await queue.join()
        finally:
            # Send stop signals to workers
            for _ in range(num_workers):
                await queue.put(None)
            await asyncio.gather(*workers, return_exceptions=True)

    log.info(f"Fast upload complete: {os.path.basename(file_path)}")
    
    return types.InputFileBig(
        id=file_id,
        parts=file_total_parts,
        name=os.path.basename(file_path),
    )


def is_large(file_size: int) -> bool:
    """Check if file needs to use BigFilePart ( > 10MB)"""
    return file_size > 10 * 1024 * 1024
