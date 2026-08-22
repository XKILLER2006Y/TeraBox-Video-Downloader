# AGENTS.md

Development guide for the TeraBox/Diskwala Telegram Bot.

## Quick Start

```bash
cd /home/yash/projects/terabox-bot
cp .env.example .env        # fill in secrets
docker compose up --build    # or see "Local dev" below
```

## Local Development

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# ensure ffmpeg installed
python main.py
```

## Project Structure

```
.
├── main.py                         # FastAPI + Telethon entrypoint
├── requirements.txt
├── Dockerfile / docker-compose.yml
├── .env.example
│
├── telegram_logic/
│   ├── bot.py                      # Telethon client, queue, cache helpers
│   ├── helpers.py                  # URL extractors, formatters
│   ├── queue.py                    # FloodWait-aware message queue
│   ├── progress_callbacks.py       # Download/upload progress updaters
│   ├── terabox_exp.py              # /exp /exphd pipeline
│   ├── terabox_trad.py             # /get pipeline
│   ├── diskwala.py                 # /dw pipeline
│   └── commands/                   # Bot command handlers
│       ├── get.py, exp.py, diskwala.py, settings.py
│       ├── broadcast.py, recent.py, cancel_download.py
│       ├── random.py, opinion.py, start.py
│
├── teraboxDL/                      # Direct TeraBox resolver
│   ├── terabox_dl.py               # jsToken + HLS chunk discovery
│   ├── public_api.py               # Multi-part downloader, stream support
│   └── stream_downloader.py        # HLS/DASH segment downloader
│
├── diskwalaDL/                     # Direct Diskwala resolver
│   ├── diskwala_dl.py              # Telethon Mini App auth + API
│   └── public_api.py               # Proxy fallback wrapper
│
├── terabox/                        # Legacy TeraBox code (proxy-based)
│   ├── core_pipeline.py
│   ├── internal_helpers.py
│   └── public_api.py
│
├── firebase_db/                    # Firestore data layer
│   ├── db.py                       # Firebase init (env-var or file)
│   ├── cache.py                    # Video cache (surl -> msg_id)
│   └── users.py                    # User tracking + mode prefs
│
├── test_e2e.py                     # End-to-end tests (20/21 passing)
├── test_bot.py                     # Telegram Bot API integration test
└── tests/                          # Additional test scripts
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token from BotFather |
| `APP_ID` | Yes | Telegram API ID from my.telegram.org |
| `API_HASH` | Yes | Telegram API hash |
| `SESSION` | No | Telethon user session string (for direct Diskwala) |
| `STORAGE_GROUP_ID` | Yes | Telegram group ID for video cache (prefix `-100`) |
| `ADMIN_ID` | No | Telegram user ID for admin commands |
| `FIREBASE_SECRETS` | Yes | Firebase service account JSON |
| `COOKIES1..N` | No | TeraBox cookies (comma-separated key=value pairs) |
| `DISKWALA_PROXY_URL` | No | Scraper proxy endpoint |
| `DISKWALA_API_KEY` | No | API key for the proxy |
| `PORT` | No | FastAPI port (default: 3000) |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/exp <url>` | Download TeraBox video (experimental) |
| `/exphd <url>` | Download TeraBox HD video |
| `/get <url>` | Download TeraBox video (legacy) |
| `/dw <url>` | Download Diskwala video |
| `/random` | Random video from cache |
| `/settings` | View/set download mode |
| `/op <message>` | Send feedback to admin |
| `/recent` | [Admin] Show recent users |
| `/broadcast` | [Admin] Broadcast message |

## Download Modes

- **exp** — TeraBox via direct HLS chunk discovery (default)
- **exphd** — TeraBox direct HD (requires premium cookies)
- **get** — TeraBox via proxy (legacy)
- **dw** — Diskwala via Telethon Mini App or proxy

## Adding a New Command

1. Create `telegram_logic/commands/newcmd.py`
2. Define `@bot.on(events.NewMessage(pattern="/newcmd"))` handler
3. Import it in `telegram_logic/commands/__init__.py`
4. Add `BotCommand` to `main.py`'s `default_commands` list

## Testing

```bash
# Unit / E2E tests
python test_e2e.py

# Full test with real Telegram bot (requires bot running on server)
python test_bot.py
```

## Deployment

```bash
docker compose up --build -d
```

Health check: `GET /ping` returns `pong`.
Logs: `docker compose logs -f telegram-bot`

## Key Architecture Decisions

- **Telethon over Pyrogram** — Telethon handles flood-waits natively with its queue system; Pyrogram was removed in commit `65e6a18`
- **Firestore over Gist** — Gist approach hit rate limits; Firestore scales naturally
- **Direct resolution first** — Both TeraBox and Diskwala try direct API calls before falling back to proxies
- **HLS chunk discovery** — TeraBox serves video as HLS segments; the bot discovers all chunks and builds a local M3U8 playlist, then ffmpeg remuxes to MP4
- **Single upload, double send** — Files upload to storage group once, then forwarded to users (saves bandwidth)
