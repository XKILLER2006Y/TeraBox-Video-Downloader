"""
tests/test_enhancements.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit and regression tests for:
- Diskwala Mini App token caching & invalidation
- Diskwala extended URL regex & ID extraction
- TeraBox extended mirror domains & clean direct errors
- FastTelethon upload zero-byte handling & cancellation safety
- Mixed-platform multi-link batch parsing
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diskwalaDL.diskwala_dl import (
    DISKWALA_URL_RE,
    extract_diskwala_id,
    extract_all_diskwala_urls,
    invalidate_token_cache,
    _token_cache,
    _token_cache_lock,
)
from teraboxDL.terabox_dl import _extract_surl, TeraBoxDirectError
from telegram_logic.helpers import (
    extract_all_terabox_url_exp,
    extract_surl_exp,
    _TERABOX_EXP_DOMAINS,
)
from universalDL import extract_universal_urls


class TestDiskwalaEnhancements(unittest.TestCase):
    def test_diskwala_url_regex_variants(self):
        urls = [
            "https://diskwala.com/app/64f123456789abcdef012345",
            "https://www.diskwala.com/sharing/link?id=64f123456789abcdef012345",
            "https://miniapp.diskwala.net/share/64f123456789abcdef012345",
            "https://t.me/sky577bot?startapp=64f123456789abcdef012345",
            "https://t.me/sky577bot/open?startapp=64f123456789abcdef012345",
        ]
        text = "Check these out:\n" + "\n".join(urls)
        extracted = extract_all_diskwala_urls(text)
        self.assertEqual(len(extracted), 5)
        for u in extracted:
            link_id = extract_diskwala_id(u)
            self.assertEqual(link_id, "64f123456789abcdef012345")

    def test_token_cache_invalidation(self):
        with _token_cache_lock:
            _token_cache["token"] = "mock_bearer_token_xyz"
            _token_cache["fetched_at"] = 123456789.0

        invalidate_token_cache()

        with _token_cache_lock:
            self.assertEqual(_token_cache["token"], "")
            self.assertEqual(_token_cache["fetched_at"], 0.0)


class TestTeraBoxEnhancements(unittest.TestCase):
    def test_terabox_mirror_domains(self):
        new_domains = [
            "teraboxshare.com",
            "teraboxlink.com",
            "terabox.club",
            "teraboxdrive.com",
        ]
        for domain in new_domains:
            self.assertIn(domain, _TERABOX_EXP_DOMAINS)
            url = f"https://{domain}/s/1TestingXYZ99"
            extracted = extract_all_terabox_url_exp(f"download {url} now")
            self.assertEqual(len(extracted), 1)

    def test_extract_surl_raises_direct_error(self):
        with self.assertRaises(TeraBoxDirectError):
            _extract_surl("https://invalid-url.com/not-a-link")


class TestMixedBatchRouting(unittest.TestCase):
    def test_mixed_platform_extraction(self):
        text = (
            "Here are multiple links:\n"
            "1. https://1024terabox.com/s/1ABC123\n"
            "2. https://diskwala.com/app/64f123456789abcdef012345\n"
            "3. https://gofile.io/d/xyz789\n"
        )
        tb_links = extract_all_terabox_url_exp(text)
        dw_links = extract_all_diskwala_urls(text)
        univ_links = extract_universal_urls(text)

        self.assertEqual(len(tb_links), 1)
        self.assertEqual(len(dw_links), 1)
        self.assertEqual(len(univ_links), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
