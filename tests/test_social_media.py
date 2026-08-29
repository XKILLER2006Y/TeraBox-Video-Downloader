"""
tests/test_social_media.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for social media URL extraction (YouTube, Instagram, TikTok,
Twitter/X, Facebook, Reddit, etc.) and media_info metadata/attribute builder.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy env vars for bot initialization during tests
os.environ.setdefault("BOT_TOKEN", "mock_token")
os.environ.setdefault("APP_ID", "12345")
os.environ.setdefault("API_HASH", "mock_hash")

# Mock firebase if not installed in local environment
for mod in [
    "firebase_admin", "firebase_admin.credentials", "firebase_admin.firestore",
    "google", "google.cloud", "google.cloud.firestore", "google.cloud.firestore_v1",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from telegram_logic.social_dl import is_social_url, extract_all_social_urls
from telegram_logic.media_info import get_video_attributes
from telegram_logic.helpers import extract_all_terabox_url_exp
from diskwalaDL.public_api import extract_all_diskwala_urls


class TestSocialMediaExtraction(unittest.TestCase):
    def test_social_url_patterns(self):
        samples = [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
            ("https://youtu.be/dQw4w9WgXcQ", True),
            ("https://www.instagram.com/reel/C3_mock123/?igsh=xyz", True),
            ("https://www.tiktok.com/@user/video/1234567890", True),
            ("https://x.com/user/status/1234567890", True),
            ("https://twitter.com/user/status/1234567890", True),
            ("https://www.facebook.com/watch/?v=123456789", True),
            ("https://www.reddit.com/r/videos/comments/xyz/sample_video/", True),
            ("https://pinterest.com/pin/123456789/", True),
            ("https://terabox.com/s/1ABC123", False),
            ("https://diskwala.com/app/123", False),
            ("https://gofile.io/d/123", False),
        ]
        for url, expected in samples:
            self.assertEqual(is_social_url(url), expected, f"Failed for {url}")

    def test_extract_all_social_urls_mixed_text(self):
        text = (
            "Here is a cool reel: https://www.instagram.com/reel/C3_mock123/ "
            "and a YouTube video: https://youtu.be/dQw4w9WgXcQ "
            "and TeraBox: https://1024terabox.com/s/1ABC123 "
            "and Diskwala: https://diskwala.com/app/64f123456789abcdef012345"
        )
        social_links = extract_all_social_urls(text)
        tb_links = extract_all_terabox_url_exp(text)
        dw_links = extract_all_diskwala_urls(text)

        self.assertEqual(len(social_links), 2)
        self.assertIn("https://www.instagram.com/reel/C3_mock123/", social_links)
        self.assertIn("https://youtu.be/dQw4w9WgXcQ", social_links)
        self.assertEqual(len(tb_links), 1)
        self.assertEqual(len(dw_links), 1)


class TestMediaInfoAttributes(unittest.TestCase):
    def test_video_attributes_builder(self):
        attrs = get_video_attributes(
            "dummy_video.mp4",
            duration=125,
            width=1920,
            height=1080,
        )
        self.assertEqual(len(attrs), 1)
        attr = attrs[0]
        self.assertEqual(attr.duration, 125)
        self.assertEqual(attr.w, 1920)
        self.assertEqual(attr.h, 1080)
        self.assertTrue(attr.supports_streaming)


if __name__ == "__main__":
    unittest.main(verbosity=2)
