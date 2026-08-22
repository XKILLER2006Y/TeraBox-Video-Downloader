"""
Test script: sends commands to the TeraBox bot via Telegram Bot API
and monitors responses.
"""
import requests
import time
import json
import sys
import os
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("ADMIN_ID") or os.getenv("TEST_CHAT_ID", "")
if not BOT_TOKEN or not CHAT_ID:
    sys.exit("Set BOT_TOKEN and ADMIN_ID (or TEST_CHAT_ID) in .env first.")
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_command(text):
    """Send a message to the bot."""
    r = requests.post(f"{BASE}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
    }, timeout=30)
    print(f"  -> Sent: {text}")
    print(f"  -> Status: {r.status_code}")
    return r.json()

def get_updates(offset=None, timeout=5):
    """Poll for new messages."""
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    r = requests.get(f"{BASE}/getUpdates", params=params, timeout=timeout + 10)
    return r.json()

def drain_updates():
    """Clear any pending updates."""
    r = get_updates(timeout=1)
    if r.get("ok") and r.get("result"):
        return r["result"][-1]["update_id"] + 1
    return None

def collect_responses(duration=30):
    """Collect bot responses for `duration` seconds."""
    responses = []
    offset = drain_updates()
    end = time.time() + duration
    while time.time() < end:
        r = get_updates(offset=offset, timeout=3)
        if r.get("ok") and r.get("result"):
            for update in r["result"]:
                msg = update.get("message", {})
                text = msg.get("text", "")
                from_bot = msg.get("from", {}).get("is_bot", False)
                if from_bot and text:
                    responses.append(text)
                    print(f"  <- Bot: {text[:200]}")
                offset = update["update_id"] + 1
        time.sleep(0.5)
    return responses


print("=" * 60)
print("TEST 1: /start command")
print("=" * 60)
send_command("/start")
time.sleep(2)
responses = collect_responses(duration=10)
if responses:
    print(f"  ✓ Got {len(responses)} response(s)")
else:
    print("  ✗ No response")

print()
print("=" * 60)
print("TEST 2: /settings command")
print("=" * 60)
send_command("/settings")
time.sleep(2)
responses = collect_responses(duration=10)
if responses:
    print(f"  ✓ Got {len(responses)} response(s)")
else:
    print("  ✗ No response")

print()
print("=" * 60)
print("TEST 3: /exp with a test TeraBox URL")
print("=" * 60)
# Use a known public TeraBox share link for testing
TEST_URL = "https://terabox.com/s/1Y3bMGa9rA8i7GRvnZ-Vm5UqjQtiQ-2Cx"
send_command(f"/exp {TEST_URL}")
time.sleep(3)
print("  Collecting responses (up to 120s - chunk discovery takes time)...")
responses = collect_responses(duration=120)
if responses:
    print(f"  ✓ Got {len(responses)} response(s)")
else:
    print("  ✗ No response")

print()
print("=" * 60)
print("TEST 4: /exp without URL (should show usage)")
print("=" * 60)
send_command("/exp")
time.sleep(2)
responses = collect_responses(duration=10)
if responses:
    print(f"  ✓ Got {len(responses)} response(s)")
else:
    print("  ✗ No response")

print()
print("All tests complete.")
