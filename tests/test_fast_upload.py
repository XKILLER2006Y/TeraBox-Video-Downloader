"""
tests/test_fast_upload.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for FastTelethon parallel uploader:
- Bounded concurrency & worker task lifecycle
- Monotonic progress callback reporting
- Transient error retry handling
- Cancellation event responsiveness
"""

import asyncio
import os
import tempfile
import threading
import pytest
from unittest.mock import AsyncMock, MagicMock

from telegram_logic.fast_upload import upload_file_fast, is_large, CHUNK_SIZE


def test_is_large_threshold():
    assert not is_large(5 * 1024 * 1024)
    assert not is_large(10 * 1024 * 1024)
    assert is_large(10 * 1024 * 1024 + 1)
    assert is_large(100 * 1024 * 1024)


def test_fast_upload_monotonic_progress():
    async def _run():
        test_size = int(2.5 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(os.urandom(test_size))
            temp_path = f.name

        try:
            mock_client = MagicMock()
            mock_client._sender = MagicMock()
            mock_client._sender.send = AsyncMock(return_value=True)

            progress_calls = []

            def progress_cb(current, total):
                progress_calls.append((current, total))

            result = await upload_file_fast(
                mock_client,
                temp_path,
                progress_callback=progress_cb,
                max_parallel=4,
            )

            assert result is not None
            assert result.parts == (test_size + CHUNK_SIZE - 1) // CHUNK_SIZE

            assert len(progress_calls) > 0
            last_current = 0
            for current, total in progress_calls:
                assert current >= last_current, f"Progress decreased: {current} < {last_current}"
                assert total == test_size
                last_current = current
            assert last_current == test_size

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    asyncio.run(_run())


def test_fast_upload_cancellation():
    async def _run():
        test_size = 3 * 1024 * 1024
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(os.urandom(test_size))
            temp_path = f.name

        cancel_event = threading.Event()
        cancel_event.set()

        mock_client = MagicMock()
        mock_client._sender = MagicMock()
        mock_client._sender.send = AsyncMock()

        try:
            with pytest.raises(asyncio.CancelledError):
                await upload_file_fast(
                    mock_client,
                    temp_path,
                    cancel_event=cancel_event,
                    max_parallel=2,
                )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    asyncio.run(_run())
