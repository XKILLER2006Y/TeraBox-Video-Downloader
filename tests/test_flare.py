"""
tests/test_flare.py
~~~~~~~~~~~~~~~~~~~
Unit and integration tests for Flare / CashSnap / HugeBox downloader module.
"""

import unittest
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from flareDL import (
    extract_flare_id,
    extract_all_flare_urls,
    get_flare_info,
    FLARE_URL_RE,
)
from flareDL.flare_dl import decrypt_flare_stream_url, AES_KEY, AES_IV


class TestFlarePatterns(unittest.TestCase):

    def test_extract_flare_id(self):
        urls = [
            ("https://flareobhx.com/s/2092601086832676866", "2092601086832676866"),
            ("https://www.flarekkox.com/s/1234567890", "1234567890"),
            ("http://hugeboxstack.com/s/abc_xyz-123", "abc_xyz-123"),
            ("https://cshsnpcwio.com/s/999888777", "999888777"),
        ]
        for url, expected_id in urls:
            with self.subTest(url=url):
                self.assertEqual(extract_flare_id(url), expected_id)

    def test_extract_all_flare_urls(self):
        text = (
            "Check this out https://flareobhx.com/s/2092601086832676866 and "
            "also https://hugeboxlightning.com/s/11223344 for more videos!"
        )
        found = extract_all_flare_urls(text)
        self.assertEqual(len(found), 2)
        self.assertIn("https://flareobhx.com/s/2092601086832676866", found)
        self.assertIn("https://hugeboxlightning.com/s/11223344", found)

    def test_decrypt_flare_stream_url(self):
        # Create test plaintext
        expected_url = "https://www.pbcshsnp.com/xbox/123/video.m3u8"
        pt_bytes = expected_url.encode("utf-8")
        pad_len = 16 - (len(pt_bytes) % 16)
        pt_padded = pt_bytes + bytes([pad_len] * pad_len)

        cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV), backend=default_backend())
        enc = cipher.encryptor()
        ct = enc.update(pt_padded) + enc.finalize()
        ct_b64 = base64.b64encode(ct).decode("utf-8")

        decrypted = decrypt_flare_stream_url(ct_b64)
        self.assertEqual(decrypted, expected_url)

    def test_get_flare_info_live(self):
        target_url = "https://flareobhx.com/s/2092601086832676866"
        info = get_flare_info(target_url)
        self.assertIn("download_url", info)
        self.assertTrue(info["download_url"].startswith("https://"))
        self.assertTrue(info["download_url"].endswith(".m3u8") or ".mp4" in info["download_url"])
        self.assertEqual(info["share_id"], "2092601086832676866")
        self.assertGreater(info["size"], 0)
        self.assertTrue(info["filename"].endswith(".mp4"))


if __name__ == "__main__":
    unittest.main()
