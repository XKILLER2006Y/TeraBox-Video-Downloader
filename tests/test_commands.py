"""
tests/test_commands.py
~~~~~~~~~~~~~~~~~~~~~~
Tests for Telegram bot command registrations and argument parsers.
"""

import pytest
from telegram_logic.helpers import parse_comp_flag, parse_quality, parse_mp3_bitrate, DEFAULT_QUALITY


def test_parse_comp_flag():
    text, comp = parse_comp_flag("https://1024tera.com/s/1abc comp")
    assert comp is True
    assert text == "https://1024tera.com/s/1abc"

    text, comp = parse_comp_flag("https://1024tera.com/s/1abc")
    assert comp is False
    assert text == "https://1024tera.com/s/1abc"


def test_parse_quality():
    text, q = parse_quality("https://1024tera.com/s/1abc 720p")
    assert q == "M3U8_AUTO_720"
    assert text == "https://1024tera.com/s/1abc"

    text, q = parse_quality("https://1024tera.com/s/1abc 1080p")
    assert q == "M3U8_AUTO_1080"
    assert text == "https://1024tera.com/s/1abc"

    text, q = parse_quality("https://1024tera.com/s/1abc")
    assert q == DEFAULT_QUALITY
    assert text == "https://1024tera.com/s/1abc"


def test_parse_mp3_bitrate():
    text, br = parse_mp3_bitrate("https://1024tera.com/s/1abc 320")
    assert br == 320
    assert text == "https://1024tera.com/s/1abc"

    text, br = parse_mp3_bitrate("https://1024tera.com/s/1abc 128")
    assert br == 128
    assert text == "https://1024tera.com/s/1abc"

    text, br = parse_mp3_bitrate("https://1024tera.com/s/1abc")
    assert br is None
    assert text == "https://1024tera.com/s/1abc"
