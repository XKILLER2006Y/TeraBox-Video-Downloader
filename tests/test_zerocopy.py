"""
tests/test_zerocopy.py
~~~~~~~~~~~~~~~~~~~~~~
Tests for stream downloader, HLS parsing, and zero-copy multipart download logic.
"""

import os
import tempfile
import threading
import pytest
from unittest.mock import patch, MagicMock

from teraboxDL.stream_downloader import (
    is_streaming_manifest,
    _parse_m3u8_segments,
    _download_direct_file_multipart,
)


def test_is_streaming_manifest():
    assert is_streaming_manifest("https://example.com/stream/index.m3u8")
    assert is_streaming_manifest("https://example.com/video.mpd")
    assert is_streaming_manifest("https://example.com/hls/segment/playlist.m3u8")
    assert is_streaming_manifest("#EXTM3U\n#EXTINF:10,\nseg1.ts")
    assert not is_streaming_manifest("https://example.com/video.mp4")
    assert not is_streaming_manifest("https://example.com/Dashcam_2024.mp4")


def test_parse_m3u8_segments():
    manifest = """#EXTM3U
#EXT-X-VERSION:3
#EXTINF:10.0,
seg1.ts
#EXTINF:10.0,
https://cdn.example.com/seg2.ts
"""
    base_url = "https://example.com/hls/index.m3u8"
    segments = _parse_m3u8_segments(manifest, base_url)
    assert len(segments) == 2
    assert segments[0] == "https://example.com/hls/seg1.ts"
    assert segments[1] == "https://cdn.example.com/seg2.ts"


def test_download_direct_file_multipart_mock():
    test_size = 1024 * 1024  # 1MB
    chunk_data = b"X" * (test_size // 4)

    with tempfile.NamedTemporaryFile(delete=False) as f:
        out_path = f.name

    try:
        mock_response = MagicMock()
        mock_response.status_code = 206
        mock_response.iter_content = MagicMock(return_value=[chunk_data])
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        progress_records = []
        def on_prog(done, total):
            progress_records.append((done, total))

        with patch("teraboxDL.stream_downloader._build_session", return_value=mock_session):
            _download_direct_file_multipart(
                "https://example.com/video.mp4",
                out_path,
                test_size,
                progress_callback=on_prog,
                num_threads=4,
            )

        assert os.path.getsize(out_path) == test_size
        assert len(progress_records) > 0
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)
