"""Quick test for stream_downloader."""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from teraboxDL.stream_downloader import download_from_stream_url, is_streaming_manifest

STREAM_URL = "https://api.teraboxdl.site/get_m3u8_stream_fast?token=hRi-r-1ThseY8LXrQ5yBt2udvEbbdEDqPNFyaaPKJNz3ynse0sQeS5OhHTRnGcJ3BAnA5HoGnNZvhttUL-mOBuUF4G0wHl_SQFK1cwCokQ4EOTZpj0lxZcAvfkip7lIOBihiNzwddu5vxZcWE7GApMiMPisi2Dmw0r4SThoQpAturqx2JPYNhoP4c_3a6nIThcE3a7sfJQrUS5CLyWzVA0ht5tHuYS1UHpNjntUftGLOenq249mwHNQfCNTnfgdtbUBITpzE-qWd0d3BljJCY5Qt6HRejicUhyDOKNREPkdFopKPqZde4F-ghk94r-3-9MW1PUVXH7kiic9yz6VfrxV3JOIcguRvqUxfzZWUJ5Ou8Fv7m24je6zRh6_-2Cq7K7xCcc7_h0xMhv27cF2TM3oQHcH4rug1EtvSqyCjWgIcmEDO1SaDUMyWOgaQC5CQ6QMispbRxZR85nXLZ2SNXPPtOhOaKtX2eptOiU7_O6IHa22vw8k5YEXi8Awevm0WvrC5o0WYR_nSVGHNqVR01UAO27rdFth1XhmTZ0_6ZHSTaakgvghsveuAVgiKDcl9P4RBEAO8HunAwIHnzmxF5VMnVYnLU-31_0G67i4-sV-kkvvw8x4mt0XV0PdCV9pwDgn9YaizVTlxEk2pTBRc-puu4u4jZFmbuB5WW-fYUXm4v_Jnbi1DKtyD0w82NPgSBg5n9Q"

OUTPUT = os.path.join("storage", "test_stream_output.mp4")

# Clean up previous test
if os.path.exists(OUTPUT):
    os.remove(OUTPUT)

print(f"URL: {STREAM_URL[:80]}...")
print(f"Is streaming manifest: {is_streaming_manifest(STREAM_URL)}")
print(f"Output: {OUTPUT}")
print()

try:
    result = download_from_stream_url(STREAM_URL, OUTPUT)
    size_mb = os.path.getsize(result) / (1024 * 1024)
    print(f"\nSUCCESS! Downloaded to: {result} ({size_mb:.2f} MB)")
except Exception as e:
    print(f"\nFAILED: {e}")
    sys.exit(1)
