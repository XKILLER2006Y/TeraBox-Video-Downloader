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
│   ├── bot.py                      # Telethon client, queue, cache helpers, shutdown drain
│   ├── helpers.py                  # URL extractors, formatters, quality/size/batch caps
│   ├── queue.py                    # FloodWait-aware message queue
│   ├── rate_limit.py               # Per-user retry budget (sliding window)
│   ├── progress_callbacks.py       # Download/upload progress updaters
│   ├── terabox_exp.py              # /exp /exphd pipeline
│   ├── diskwala.py                 # /dw pipeline
│   └── commands/                   # Bot command handlers
│       ├── exp.py, diskwala.py, settings.py, status.py
│       ├── broadcast.py, recent.py, cancel_download.py
│       ├── random.py, opinion.py, start.py, universal.py
│
├── teraboxDL/                      # Direct TeraBox resolver
│   ├── errors.py                   # Canonical exception hierarchy
│   ├── terabox_dl.py               # jsToken + HLS chunk discovery + cookie rotation
│   ├── public_api.py               # Multi-part downloader, stream support
│   └── stream_downloader.py        # HLS/DASH segment downloader
│
├── diskwalaDL/                     # Direct Diskwala resolver
│   ├── errors.py                   # Diskwala exception types (from teraboxDL.errors base)
│   ├── diskwala_dl.py              # Telethon Mini App auth + API
│   └── public_api.py               # Proxy fallback wrapper
│
├── firebase_db/                    # Firestore data layer
│   ├── db.py                       # Firebase init (env-var or file)
│   ├── cache.py                    # Video cache (surl -> msg_id)
│   └── users.py                    # User tracking + mode prefs (legacy modes migrated)
│
├── test_e2e.py                     # End-to-end tests
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
| `COOKIES1..N` | No | TeraBox cookies — form a rotation pool; rate-limited cookies are auto-skipped |
| `DISKWALA_PROXY_URL` | No | Scraper proxy endpoint |
| `DISKWALA_API_KEY` | No | API key for the proxy |
| `PORT` | No | FastAPI port (default: 3000) |
| `MAX_FAILURES_PER_WINDOW` | No | Retry budget: failures allowed per window (default 5, 0 = off) |
| `FAILURE_WINDOW_SECONDS` | No | Sliding window for failure counting (default 600) |
| `FAILURE_COOLDOWN_SECONDS` | No | Block duration once budget exhausted (default 600) |
| `MAX_LINKS_PER_MESSAGE` | No | Max links processed per message (default 5) |
| `MAX_FILE_SIZE_MB` | No | Skip files larger than this (default 0 = unlimited) |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/exp <url> [quality]` | Download TeraBox video (quality: 360p/480p/720p/1080p) |
| `/exphd <url>` | Download TeraBox HD video |
| `/dw <url>` | Download Diskwala video |
| `/dl <url>` | Download from any supported host (GoFile, StreamTape, Dood, MediaFire, …) |
| `/random` | Random video from cache |
| `/status` | Bot health & stats (admin detail when sent by ADMIN_ID) |
| `/stats` | Your personal download stats (admins also see global) |
| `/history` | Your recent downloads |
| `/settings` | View/set download mode |
| `/op <message>` | Send feedback to admin |
| `/recent` | [Admin] Show recent users |
| `/broadcast` | [Admin] Broadcast message |

## Download Modes

- **exp** — TeraBox via direct HLS chunk discovery (default)
- **exphd** — TeraBox direct HD (requires premium cookies)
- **dw** — Diskwala via Telethon Mini App or proxy

**Inline mode** — after enabling *Inline Mode* via @BotFather, users can
type `@botusername <link>` in any chat; selecting the result posts the
link and triggers the normal download pipeline.

> The legacy **get** mode (proxy pipeline) was removed along with the old
> `terabox/` module. Users whose saved mode was `get` are transparently
> migrated to `exp`.

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
