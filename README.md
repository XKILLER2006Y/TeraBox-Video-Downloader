# TeraBox / Diskwala Telegram Bot

A Telegram bot that downloads videos from TeraBox and Diskwala cloud storage services and delivers them directly to Telegram chats.

## Features

- **TeraBox downloads** — Resolve share links via direct HLS chunk discovery
- **Diskwala downloads** — Resolve via Telethon Mini App API or scraper proxy
- **Universal downloads** — `/dl` for GoFile, StreamTape, Doodstream, MediaFire, Catbox & more
- **Multiple modes** — `/exp`, `/exphd`, `/dw` with per-user preferences
- **Quality selection** — `/exp <url> 720p` picks a resolution (auto-fallback to best)
- **Batch links** — Send several URLs in one message (capped per message)
- **Video caching** — Downloaded videos stored in a Telegram group for instant re-delivery
- **Cancel mid-download** — Inline cancel button stops download/upload in progress
- **Flood-wait handling** — Automatic queuing when Telegram rate-limits the bot
- **Cookie rotation** — TeraBox cookies auto-rotate on 429/403 rate-limits
- **Retry budget** — Per-user failure limits prevent abuse
- **Graceful shutdown** — In-flight downloads finish before restarts
- **Multi-part download** — Parallel byte-range connections for 4x throughput
- **HLS to MP4** — Automatic remuxing via ffmpeg

## Requirements

- Python 3.12+
- ffmpeg
- Telegram Bot Token (from @BotFather)
- Telegram API credentials (from my.telegram.org)
- Firebase project with Firestore enabled

## Setup

```bash
# Clone and configure
git clone <repo-url>
cd terabox-bot
cp .env.example .env
# Edit .env with your credentials

# Run with Docker
docker compose up --build -d

# Or run locally
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Environment Variables

See [`.env.example`](.env.example) for all available configuration options.

## Architecture

```
User sends URL
      │
      ▼
┌─────────────┐
│ main.py     │  FastAPI + Telethon entrypoint
│ (router)    │  Detects URL type, routes to handler
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│ terabox_exp │     │ diskwala.py │
└──────┬──────┘     └──────┬──────┘
       │                   ▼
       │           ┌──────────────┐
       │           │ diskwalaDL/  │  Direct resolution
       │           │ public_api.py│  or proxy fallback
       │           └──────┬───────┘
       ▼                  │
┌──────────────┐          │
│ teraboxDL/   │          │
│ terabox_dl.py│          │
│ public_api.py│          │
└──────┬───────┘          │
       │                  │
       ▼                  ▼
  ┌────────────────────────┐
  │   Download → Storage   │
  │   group → User         │
  └────────────────────────┘
```

## Testing

```bash
python test_e2e.py   # 20/21 tests passing
```

## License

Private project.
