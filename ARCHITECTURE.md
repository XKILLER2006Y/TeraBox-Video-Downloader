# ARCHITECTURE.md

System architecture for the TeraBox/Diskwala Telegram Bot.

## High-Level Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Cloud                        │
│  (User messages, Bot API, Telethon userbot, media)      │
└──────────┬──────────────────────────────┬───────────────┘
           │                              │
     ┌─────▼──────┐              ┌────────▼────────┐
     │ Bot Client  │              │ Storage Group   │
     │ (Telethon)  │              │ (cache videos)  │
     └─────┬──────┘              └────────▲────────┘
           │                              │
     ┌─────▼──────────────────────────────┼───────────┐
     │           main.py (FastAPI)        │           │
     │  ┌──────────┐  ┌────────────────┐  │           │
     │  │ Router   │  │ Command Reg.   │  │           │
     │  └────┬─────┘  └────────────────┘  │           │
     │       │                             │           │
     │  ┌────▼─────────────────────────────┼────────┐  │
     │  │       telegram_logic/           │        │  │
     │  │  ┌──────────┐ ┌──────────┐ ┌────▼─────┐ │  │
     │  │  │terabox_exp│ │terabox_trad│ │diskwala │ │  │
     │  │  └─────┬────┘ └─────┬────┘ └────┬─────┘ │  │
     │  │        │             │            │       │  │
     │  │  ┌─────▼─────────────▼────────────▼─────┐ │  │
     │  │  │         Download Pipeline            │ │  │
     │  │  │  cache → metadata → download → upload │ │  │
     │  │  └──────────────────────────────────────┘ │  │
     │  └───────────────────────────────────────────┘  │
     └─────────────────────────────────────────────────┘
           │                              ▲
     ┌─────▼──────┐              ┌────────┴────────┐
     │  Resolvers  │              │   firebase_db/  │
     │  teraboxDL/ │              │  cache, users   │
     │  diskwalaDL/│              └─────────────────┘
     └─────┬──────┘
           │
     ┌─────▼──────────────────────────────────┐
     │          External Services              │
     │  TeraBox API │ Diskwala API │ ffmpeg    │
     └─────────────────────────────────────────┘
```

## Component Details

### 1. Entry Point (`main.py`)

FastAPI application with Telethon bot lifecycle.

- `lifespan()` — starts bot task, yields, cleans up
- `@bot.on(events.NewMessage)` — global tracker (user analytics)
- `handle_message()` — URL detection + mode routing
- `/ping` — health check endpoint

### 2. Router Logic (`handle_message`)

```
message received
  → skip if starts with "/"
  → get_user_mode(chat_id) from Firestore cache
  → if mode == "get":  extract TeraBox SURLs → process_terabox()
  → if mode == "exp":  extract TeraBox URLs  → process_terabox_experimental()
  → if mode == "exphd": extract TeraBox URLs → process_terabox_experimental(is_hd=True)
  → if mode == "dw":   extract Diskwala URLs → process_diskwala()
  → wrong-source hints if URL type doesn't match mode
```

### 3. Download Pipeline (all three handlers follow this pattern)

```
Phase 1: Cache Lookup
  → search Firestore cache by SURL/link ID
  → if hit: re-send cached message, done

Phase 2: Metadata
  → call resolver (direct or proxy)
  → get filename, size, download URL

Phase 3: Download
  → detect URL type (HLS manifest or direct file)
  → if HLS: download segments → concatenate → remux to MP4
  → if direct: multi-part byte-range or single-stream

Phase 4: Upload to Storage
  → upload to Telegram storage group (if STORAGE_GROUP_ID set)
  → cache SURL → message_id in Firestore

Phase 5: Deliver to User
  → send video with caption (filename, size, timings)
  → clean up temp files
```

### 4. TeraBox Direct Resolver (`teraboxDL/terabox_dl.py`)

```
Input: TeraBox share URL
  │
  ├─ _extract_surl(url) → surl
  ├─ _load_session(cookies) → requests.Session
  ├─ _get_js_token(session, surl) → jsToken
  │    └─ GET /wap/share/filelist → parse HTML for fn("TOKEN")
  ├─ _get_share_info(session, js_token, surl) → metadata
  │    └─ GET /api/shorturlinfo → JSON with file list
  └─ _discover_all_hls_chunks(...) → local M3U8 path
       └─ Poll /share/streaming → collect segment URLs
```

### 5. Diskwala Direct Resolver (`diskwalaDL/diskwala_dl.py`)

```
Input: Diskwala share URL
  │
  ├─ _get_telethon_client() → TelegramClient (from SESSION string)
  ├─ _get_auth_token() → Bearer token
  │    └─ RequestAppWebViewRequest → parse tgWebAppData fragment
  ├─ _start_download(url, headers) → job started
  │    └─ POST api2.diskwala.net/api/diskwala/download
  └─ _poll_status(url, headers) → {filename, size, download_url}
       └─ GET api2.diskwala.net/api/diskwala/status?link=...
```

### 6. Concurrency Model

```
                    ┌─────────────┐
                    │ MessageQueue │
                    │ (sem=20)    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         ┌────▼───┐  ┌────▼───┐  ┌────▼───┐
         │ Task 1 │  │ Task 2 │  │ Task N │  (max 20)
         └────────┘  └────────┘  └────────┘

FloodWait handling:
  - Semaphore limits concurrent work
  - FloodWait sets cooldown timer
  - New requests auto-queued during cooldown
  - Queue processes tasks when cooldown expires
```

### 7. Cache Architecture

```
Firestore "cache" collection
  ├── get    (document)  → {surl: msg_id, ...}
  ├── exp    (document)  → {surl: msg_id, ...}
  ├── exphd  (document)  → {surl: msg_id, ...}
  └── dw     (document)  → {link_id: msg_id, ...}

Key encoding: surl → Base64URL → k_ prefix (Firestore field name safe)

Search priority:
  get   → exphd → exp → get
  exp   → exphd → exp
  exphd → exphd only
  dw    → dw only
```

### 8. Download Strategies

| Strategy | When | How |
|----------|------|-----|
| HLS segment download | TeraBox direct | Discover chunks → build M3U8 → ffmpeg remux |
| Multi-part byte-range | Direct URL, Range supported | 4 parallel connections, 1MB chunks |
| Single-stream | Direct URL, no Range support | Sequential chunk download |
| ffmpeg remux | After HLS download | TS → MP4 container, `-c copy` (no re-encode) |

### 9. Error Flow

```
resolver raises TeraBoxDirectError / DiskwalaDirectError
  → handler catches it
  → user sees "❌ <friendly message>"
  → suggests trying different mode

resolver raises network error
  → handler catches it
  → user sees "❌ <error details>"
  → suggests trying again later

FloodWaitError from Telegram
  → queue sets cooldown
  → user sees "⏳ Queued, processing in ~Ns"
  → auto-processed when cooldown expires
```

### 10. Data Storage

| Store | Purpose | Schema |
|-------|---------|--------|
| Firestore `users` | User tracking | `{username, last_active, mode}` per chat_id |
| Firestore `cache` | Video cache | `{surl: msg_id}` per mode bucket |
| Telegram storage group | Video files | Messages with video media |
| Local disk | Temp downloads | `storage/` directory (cleaned after upload) |
