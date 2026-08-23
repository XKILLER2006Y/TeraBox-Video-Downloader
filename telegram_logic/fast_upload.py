"""
FastTelethon: Parallel chunked uploads/downloads to Telegram.

Based on the painor/FastTelethon gist (MIT licensed).
Uses multiple parallel senders to Telegram DCs for faster uploads.
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
    progress_callback=None,
):
    """Send a single chunk to Telegram."""
    await client._sender.send(
        functions.upload.SaveBigFilePart(
            file_id=file_id,
            file_part=file_part,
            file_part_count=file_total_parts,
            bytes=file_part,
        )
    )
    if progress_callback:
        # Calculate bytes sent for this part
        bytes_sent = (file_part + 1) * CHUNK_SIZE if file_part < file_total_parts - 1 else file_size
        progress_callback(bytes_sent, file_size)


async def upload_file_fast(
    client,
    file_path: str,
    progress_callback=None,
) -> types.InputFileBig:
    """
    Upload a file to Telegram servers using parallel chunked uploads.
    
    Args:
        client: Telethon TelegramClient instance
        file_path: Path to the file to upload
        progress_callback: Optional callback(current, total) for progress updates
    
    Returns:
        types.InputFileBig handle that can be used with send_file
    """
    file_size = os.path.getsize(file_path)
    file_id = utils.generate_random_long()
    file_total_parts = math.ceil(file_size / CHUNK_SIZE)
    
    log.info(f"Fast upload: {os.path.basename(file_path)} ({file_size / (1024*1024):.1f} MB, {file_total_parts} parts)")
    
    # Read file into memory for parallel sending
    with open(file_path, 'rb') as f:
        file_data = f.read()
    
    # Split into chunks
    chunks = []
    for i in range(file_total_parts):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, file_size)
        chunks.append(file_data[start:end])
    
    # Send chunks in parallel
    sem = asyncio.Semaphore(MAX_PARALLEL)
    
    async def _send_with_sem(part_idx, chunk):
        async with sem:
            await _send_partial(
                client,
                file_id,
                chunk,
                file_total_parts,
                file_size,
                progress_callback,
            )
    
    # Create tasks for all chunks
    tasks = [_send_with_sem(i, chunk) for i, chunk in enumerate(chunks)]
    
    # Execute all uploads concurrently
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
    
    Args:
        client: Telethon TelegramClient instance
        msg: Message containing the file to download
        out: Output file path
        progress_callback: Optional callback(current, total) for progress updates
    
    Returns:
        Path to the downloaded file
    """
    if msg.media is None:
        raise ValueError("Message has no media to download")
    
    # Get file location
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
    
    # Calculate chunk size for download (1MB chunks for download)
    download_chunk = 1024 * 1024
    total_parts = math.ceil(file_size / download_chunk)
    
    downloaded = 0
    
    with open(out, 'wb') as f:
        for part in range(total_parts):
            if part == total_parts - 1:
                # Last part might be smaller
                data = await client._get_file(loc, part, total_parts)
            else:
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
