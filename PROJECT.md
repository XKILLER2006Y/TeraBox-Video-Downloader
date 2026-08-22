# PROJECT.md

## Overview

**TeraBox/Diskwala Telegram Bot** — a production bot that downloads videos from TeraBox and Diskwala cloud services and delivers them to Telegram users.

**Status:** Production (deployed via Docker on a VPS)

## Repository

- Location: `/home/yash/projects/terabox-bot`
- Git: initialized, latest commit `65e6a18` (8 files changed, +994 lines)
- Untracked: `test_e2e.py`, `test_bot.py`, `tests/`

## Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12 (Docker), 3.14 (local dev) |
| Telegram | Telethon (userbot + bot API) |
| Database | Firebase Firestore |
| HTTP Server | FastAPI + Uvicorn |
| Deployment | Docker Compose (VPS) |
| Video Processing | ffmpeg (TS→MP4 remux) |
| Browser | requests (HTTP client) |

## Data Flow

1. **User sends link** → `main.py` router detects TeraBox/Diskwala URL
2. **Mode routing** → User's preferred mode (`exp`/`exphd`/`get`/`dw`) selects handler
3. **Cache check** → Firestore `cache` collection checked first (by mode priority)
4. **Metadata** → Share link resolved to filename, size, download URL
5. **Download** → Multi-part byte-range or HLS segment download
6. **Upload** → Uploaded to Telegram storage group, cached in Firestore
7. **Deliver** → Video sent to user with timing stats

## Key Modules

### `teraboxDL/terabox_dl.py` — Direct TeraBox Resolver
- Extracts jsToken from share page HTML
- Fetches file metadata via `/api/shorturlinfo`
- Discovers HLS chunks via `/share/streaming` endpoint
- Builds local M3U8 playlist for ffmpeg
- `TeraBoxDirectError` exception with mapped error codes

### `diskwalaDL/diskwala_dl.py` — Direct Diskwala Resolver
- Uses Telethon user session to call Mini App endpoint
- Obtains Bearer auth token from `tgWebAppData` URL fragment
- Calls `api2.diskwala.net` download + status polling APIs
- `DiskwalaDirectError` exception for all failure modes

### `firebase_db/cache.py` — Video Cache
- Firestore collection `cache`, documents: `get`/`exp`/`exphd`/`dw`
- Key encoding: URL-safe Base64 with `k_` prefix (Firestore field name constraints)
- Priority search: `get` → `exphd` → `exp` → `get`
- In-memory snapshot for `/random` (15-minute TTL)

### `firebase_db/users.py` — User Tracking
- Firestore collection `users`, one document per `chat_id`
- Fields: `username`, `last_active`, `mode`
- Write debounce: 15 minutes per user
- In-memory cache reduces Firestore reads

### `telegram_logic/queue.py` — Flood-Wait Queue
- Semaphore-based concurrency limit (20 simultaneous tasks)
- FloodWait cooldown tracking
- Auto-queue when rate-limited

## Current Capabilities

- [x] TeraBox direct resolution (HLS chunk discovery)
- [x] TeraBox proxy fallback (legacy `/get` mode)
- [x] Diskwala direct resolution (Telethon Mini App)
- [x] Diskwala proxy fallback
- [x] Video caching in Telegram storage group
- [x] Firestore-backed user preferences
- [x] Cancel button on downloads
- [x] Flood-wait auto-queue
- [x] Multi-part parallel downloads
- [x] HLS to MP4 remuxing
- [x] Docker deployment
- [x] Admin commands (`/recent`, `/broadcast`)
- [x] End-to-end tests (20/21 passing)

## Known Issues

- Session string for Telethon user account must be regenerated if Telegram session expires
- TeraBox HD mode requires premium cookies (`COOKIES1..N`) which expire periodically
- Single-stream fallback when server doesn't support Range requests (slower)
- No rate limiting on per-user request frequency

## Commit History (Recent)

| Commit | Description |
|--------|-------------|
| `65e6a18` | TeraBox error handling + Diskwala direct resolution + commit docs |
| Previous | TeraBox HLS chunk discovery, stream downloader, proxy setup |
| Earlier | Firebase migration, user tracking, cache system |
| Initial | Basic bot with proxy-only TeraBox download |
