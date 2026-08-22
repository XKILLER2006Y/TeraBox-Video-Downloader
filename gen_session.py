"""
gen_session.py — Generate Telethon StringSession for Diskwala direct resolution.

Usage:
    python gen_session.py

Prompts for phone number, OTP, and password (if 2FA enabled).
Prints the session string between SESSION_START and SESSION_END markers.
Add the session string to .env as: SESSION=<string>

Environment variables used:
    APP_ID   — Telegram API ID (from my.telegram.org)
    API_HASH — Telegram API hash
"""

import os
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

from dotenv import load_dotenv
load_dotenv()

APP_ID = int(os.getenv("APP_ID", "0"))
API_HASH = os.getenv("API_HASH", "")


async def main():
    if not APP_ID or not API_HASH:
        print("ERROR: Set APP_ID and API_HASH in .env first.")
        return

    client = TelegramClient(StringSession(), APP_ID, API_HASH)
    await client.start()  # prompts for phone, code, password
    print("\nSESSION_START")
    print(client.session.save())
    print("SESSION_END")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
