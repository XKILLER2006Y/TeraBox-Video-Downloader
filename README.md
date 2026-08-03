# TeraBox / Diskwala Video Downloader

Downloads full-length videos from **TeraBox** (and its mirror domains) and **Diskwala**, then delivers them through Telegram — caching each video once in a storage group and re-forwarding it on repeat requests.

---

## Features

- **Auto-detect links**: Paste a TeraBox or Diskwala URL anywhere in a message; it auto-downloads according to your selected mode.
- **Three download engines / four modes**:
  - **Traditional (`/get`)** — Budget-capped TS chunk collector relying on rotating cookies. *[Unstable — best for small files]*
  - **Experimental (`/exp`, `/exphd`)** — Fast extractor via a scraper proxy that resolves direct CDN links; `/exphd` targets HD. *[Recommended]*
  - **Diskwala (`/dw`)** — Resolves Diskwala share links via a scraper proxy and downloads the direct video. *[New]*
- **Expanded TeraBox domain support** (`/exp`, `/exphd`): `terabox.com`, `1024terabox.com`, `teraboxapp.com`, `freeterabox.com`, `terabox.app`, `terabox.fun`, `4funbox.co/.com`, `mirrobox.com`, `nephobox.com`, `1024tera.com`, `momerybox.com`, `tibibox.com` (with optional `www.`), in both `{base}/<something>/{SURL}` and `{base}/{SURL}` URL shapes.
- **Smart mode hints**: Sending a Diskwala link while in a TeraBox mode (or a TeraBox link while in `dw` mode) replies with the correct command / mode to switch to, instead of silently ignoring it.
- **`/random`**: Re-sends a random previously cached video.
- **`/settings`**: Switch the default auto-download mode (`get`, `exp`, `exphd`, `dw`).
- **`/op <msg>`**: Send feedback to the admin.
- **Admin Commands**:
  - **`/recent`**: Show recent users interacting with the bot.
  - **`/broadcast`**: Broadcast a message to all known users and groups.
- **Cancel button**: Inline button to abort an in-progress download at the next checkpoint.
- **Telegram-side caching**: Uploads each video once to a storage group and re-forwards on repeat requests. Firestore holds per-source cache buckets — `get`, `exp`, `exphd`, `dw`.
- **Persistent DB via Firebase Firestore**: Tracks users, chat IDs, and each user's selected mode.
- **Flood Control Queue**: Custom semaphore and async queue handling to survive `FloodWaitError` during viral moments.
- **Quality fallback**: Tries 1080p -> 720p -> 480p -> 360p automatically (on the traditional pipeline).

---

## Architecture

### 1. Command structure

Every incoming update first passes through `global_tracker` (records the user in Firestore), then splits on whether the text is a slash command or a plain message.

```mermaid
graph TD
    U["User / Group chat"] --> TR["global_tracker<br/>(track_user → Firestore)"]
    TR --> R{"text starts with '/'?"}

    R -->|"yes"| CMDS["Command handlers<br/>(telegram_logic/commands/*)"]
    R -->|"no"| PLAIN["handle_message<br/>(mode-based routing)"]

    CMDS --> S["/start — welcome + help"]
    CMDS --> EXP["/exp url — TeraBox (fast)"]
    CMDS --> EXPHD["/exphd url — TeraBox (HD)"]
    CMDS --> GET["/get url — TeraBox (traditional)"]
    CMDS --> DW["/dw url — Diskwala"]
    CMDS --> RND["/random — random cached video"]
    CMDS --> SET["/settings — switch default mode"]
    CMDS --> OP["/op msg — feedback to admin"]
    CMDS --> REC["/recent — admin only"]
    CMDS --> BRD["/broadcast — admin only"]
```

### 2. Plain-message routing (conditional logic)

For a message with no command, the bot looks up the user's mode and picks the matching URL matcher. If the expected link type is absent but the *other* type is present, it replies with a hint instead of ignoring the message.

```mermaid
flowchart TD
    P["Plain message (no slash)"] --> MODE{"get_user_mode(chat_id)"}

    MODE -->|"get"| GS["extract_all_surls()<br/>(legacy TeraBox regex)"]
    MODE -->|"exp"| ES["extract_all_terabox_url_exp()"]
    MODE -->|"exphd"| EHS["extract_all_terabox_url_exp()"]
    MODE -->|"dw"| DS["extract_all_diskwala_urls()"]

    GS -->|"TeraBox link found"| GP["process_terabox()<br/>traditional pipeline"]
    ES -->|"TeraBox link found"| EP["process_terabox_experimental()"]
    EHS -->|"TeraBox link found"| EHP["process_terabox_experimental(is_hd=True)"]
    DS -->|"Diskwala link found"| DP["process_diskwala()"]

    GS -->|"none, but Diskwala link present"| H1["Hint: use /dw or switch mode to dw"]
    ES -->|"none, but Diskwala link present"| H1
    EHS -->|"none, but Diskwala link present"| H1
    DS -->|"none, but TeraBox link present"| H2["Hint: use /exp,/exphd,/get or switch mode"]

    GS -->|"nothing relevant"| IGN["silently ignore"]
    ES --> IGN
    EHS --> IGN
    DS --> IGN
```

> The same cross-type hint logic is repeated inside the explicit command handlers — e.g. `/dw <terabox-link>` points you at `/exp`, and `/exp <diskwala-link>` points you at `/dw`.

### 3. Low-level download pipeline

`/exp`, `/exphd` and `/dw` share one pipeline (in `terabox_exp.py` / `diskwala.py`), differing only in the **metadata source**. `/get` follows an analogous path with the traditional chunk collector. All Telegram sends flow through the flood-aware queue, and every phase honours the inline **Cancel** button via a `threading.Event`.

```mermaid
flowchart TD
    IN["process_* (event, url)"] --> FL{"flood cooldown active?"}
    FL -->|"yes"| QN["queue task + notify user (~N s)"]
    FL -->|"no"| SEM["acquire semaphore (limit 20)"]

    SEM --> REG["register cancel_event in active_tasks<br/>render ❌ Cancel button"]
    REG --> CACHE{"search_in_cache(key, mode)"}
    CACHE -->|"hit"| FWD["re-forward cached video<br/>from storage group"] --> DONE["delete status message"]
    CACHE -->|"miss"| META["fetch metadata (in worker thread)"]

    META -->|"exp / exphd"| M1["get_video_info()<br/>TeraBox scraper proxy"]
    META -->|"dw"| M2["get_diskwala_info()<br/>Diskwala scraper proxy"]

    M1 --> DL["download_terabox_file_experimental()<br/>multipart / HLS+ffmpeg / direct"]
    M2 --> DL

    DL --> PRE["_pre_upload_file()<br/>upload bytes once → InputFile handle"]
    PRE --> ST{"STORAGE_GROUP_ID set?"}
    ST -->|"yes"| UP["_upload_to_storage()<br/>+ add_to_cache(key, msg_id, mode)"]
    ST -->|"no"| SEND
    UP --> SEND["send_file to user<br/>(caption: name, size, timings)"]
    SEND --> CLEAN["delete local temp files"] --> DONE

    REG -.->|"user taps Cancel"| CX["cancel_event.set()<br/>→ abort at next checkpoint"]
```

---

## Project Structure

```text
main.py                        # Entry point, FastAPI wrapper, global tracker, mode routing, command registration
.env                           # Secrets (not committed)
Dockerfile / docker-compose.yml  # Container configuration
requirements.txt               # Python package dependencies
apt.txt                        # OS-level dependencies (ffmpeg, etc.)

telegram_logic/
  bot.py                       # Telethon client + shared upload/cache/cancel helpers
  helpers.py                   # URL matchers (TeraBox legacy + experimental, Diskwala), size/duration formatting
  progress_callbacks.py        # Live progress-message editing during download & upload
  queue.py                     # Semaphore + flood-wait queue
  terabox_trad.py              # Traditional (/get) pipeline
  terabox_exp.py               # Experimental (/exp, /exphd) pipeline
  diskwala.py                  # Diskwala (/dw) pipeline
  commands/                    # Individual Telegram command handlers
    start.py                   # /start
    get.py                     # /get <url>
    experimental.py            # /exp and /exphd <url>
    diskwala.py                # /dw <url>
    random.py                  # /random
    settings.py                # /settings (download-mode switch)
    opinion.py                 # /op <msg> (feedback to admin)
    cancel_download.py         # Inline "Cancel" callback handler
    recent.py                  # /recent (Admin)
    broadcast.py               # /broadcast (Admin)

terabox/                       # Traditional (/get) API approach
  public_api.py                # Public interface for traditional pipeline
  core_pipeline.py             # Internal extraction, chunk discovery, ts download
  internal_helpers.py          # Shared utilities and custom exceptions

teraboxDL/                     # Experimental (/exp, /exphd) extractor
  terabox_dl.py                # Metadata via scraper proxy (get_video_info)
  public_api.py                # download_terabox_file_experimental (concurrent multipart downloader)
  stream_downloader.py         # HLS / direct stream downloader + ffmpeg remux

diskwalaDL/                    # Diskwala (/dw) extractor
  public_api.py                # Diskwala proxy client + URL helpers (get_diskwala_info)

firebase_db/                   # Firebase Firestore persistence
  db.py                        # Firestore client initialisation
  users.py                     # User tracking + per-user mode (get/exp/exphd/dw)
  cache.py                     # surl -> message_id cache buckets (get/exp/exphd/dw)
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- `ffmpeg` available on `PATH` (used to remux HLS `.ts` segments into `.mp4`)
- A Firebase project with Firestore enabled (service-account credentials)
- Access to the TeraBox and Diskwala scraper proxies (URLs + Diskwala API key)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_telegram_bot_token
APP_ID=your_telegram_app_id
API_HASH=your_telegram_api_hash
STORAGE_GROUP_ID=-1001234567890         # numeric ID of a private storage supergroup
ADMIN_ID=12345678                       # your user ID to access /broadcast and /recent

# Firebase Firestore (service-account JSON as a single-line string)
FIREBASE_SECRETS={"type":"service_account", ... }

# Experimental (/exp, /exphd) scraper proxy
THIRD_PARTY_TERABOXDL_URL=https://www.teraboxdl.site/
PROXY_URL=http://<proxy-host>/v1

# Diskwala (/dw) scraper proxy
DISKWALA_PROXY_URL=http://<proxy-host>/video
DISKWALA_API_KEY=your_diskwala_api_key  # sent as the x-api-key request header

# Traditional (/get) cookies (browser Cookie header string)
COOKIES1=browserid=...; TSID=...
COOKIES2=...
```

- `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `APP_ID` / `API_HASH` — from [my.telegram.org](https://my.telegram.org)
- `STORAGE_GROUP_ID` — must be a supergroup ID (starts with `-100`). The bot must be admin.
- `FIREBASE_SECRETS` — the Firestore service-account JSON, collapsed to one line; persists users, modes, and the video cache.
- `THIRD_PARTY_TERABOXDL_URL` / `PROXY_URL` — endpoints the experimental (`/exp`, `/exphd`) pipeline uses to resolve TeraBox links.
- `DISKWALA_PROXY_URL` / `DISKWALA_API_KEY` — the Diskwala (`/dw`) proxy endpoint and its `x-api-key`.
- `COOKIES1..N` — TeraBox session cookies for the traditional (`/get`) pipeline.

### 4. Add cookies (For Traditional Mode)

The bot authenticates with TeraBox using browser cookies. Save your copied cookie header directly inside `.env` under `COOKIES1` and `COOKIES2`. Or you can save them in `cookies.txt` in Netscape format.

See [Extracting Cookies](#extracting-cookies) below.

### 5. Run

Locally:
```bash
python main.py
```

Using Docker:
```bash
docker build -t terabox-bot .
docker run -d --env-file .env terabox-bot
```

---

## Limitations

1. **Telegram File Size Limit**: Telegram restricts standard bot file uploads to **50 MB** and strictly restricts using local API servers to **2 GB**. Any resulting video chunk transcoded to more than maximum limits will fail.
2. **Rate Limits & API Bans**: TeraBox API rate-limits aggressively on the traditional (`/get`) approach. We use budget limits to avoid IP bans but this may leave >1 hour videos missing a sub-segment (skip ~4 minutes).
3. **Concurrency & throughput**: The experimental (`/exp`, `/exphd`) and Diskwala (`/dw`) pipelines resolve links through external scraper proxies and download via concurrent multipart connections. A global semaphore (limit 20) plus the flood-wait queue bound how many downloads/uploads run at once; downloads are still disk- and bandwidth-bound on a low-end VPS.
4. **Proxy / link expiry**: The scraper proxies and the direct CDN links they return are time-limited. Resolution can break when TeraBox or Diskwala change their backends, necessitating proxy-side tweaks.

---

## Extracting Cookies (For Traditional pipeline)

The TeraBox traditional download pipeline requires authenticated session cookies. To extract them:

1. Open any TeraBox share link in a **desktop browser** and log in.
2. Open the same link again so the video preview loads.
3. Open **DevTools -> Network** tab while the page loads.
4. Find the top-level request to `surl?...` that returns **200 OK** (not a redirect).
5. Copy all cookies from its **Request Headers -> Cookie** field into your `.env` (as `COOKIES1`=...).

---

## Key Concepts

### What Are Chunks / Segments?

TeraBox does **not** give you a single download link for large videos. Instead, the video is internally split into **N sequential chunks** (also called "TS segments"), each roughly covering a **~4-minute window** of the video.

Each chunk is a `.ts` (MPEG Transport Stream) file named with an index suffix like `_0_ts`, `_1_ts`, `_2_ts` … `_N_ts`.  To reconstruct the full video, you must download **every** chunk in order and remux them into a single `.mp4`.

### Which Endpoints Do We Hit?

| # | Endpoint / URL | Purpose | Returns |
|---|----------------|---------|---------|
| 1 | `GET /wap/share/filelist?surl=…` | Load the share page HTML | HTML containing `jsToken` (anti-CSRF) |
| 2 | `GET /api/shorturlinfo?shorturl=…&jsToken=…` | Fetch file metadata | JSON with `shareid`, `uk`, `sign`, `timestamp`, `fs_id`, file names, sizes |
| 3 | `GET /share/streaming?…&type=M3U8_AUTO_1080&start=0` | Request HLS playlist | M3U8 text — returns **one random chunk** (see below) |
| 4 | `GET <cdn_url>/chunk_N.ts?range=0-…&len=…` | Download a single TS chunk | Raw binary `.ts` data |

> **Important:** Each chunk URL contains a **unique cryptographic signature** in its path.  You cannot fabricate or guess URLs — every chunk URL must come from an actual API response.

---

## Current Approach: Budget-Capped Collector

The current algorithm treats the problem pragmatically: **collect as many chunks as possible within a request budget, accept occasional gaps**.

### How It Works

1. **Blind poll** the streaming endpoint repeatedly (the `start` param is ignored, so we just send `start=0`)
2. **Track** discovered chunks by their unique `_N_ts` index in the URL path
3. **Stop** when either condition fires:

| Rule | Condition | Purpose |
|------|-----------|---------|
| **Early stop** | `is_complete()` AND `no_new_max_streak >= max(10, max_idx)` | Confident we have everything |
| **Budget cap** | `req_count >= max(30, max_idx × 3)`, hard capped at **100** | Prevent rate-limiting |

### `is_complete()` Logic

Returns `True` only when:
- `min(known) ≤ 1` — chunks start at index 0 or 1
- All indices between min and max are present (no gaps)

### API Request Estimates

| Video Length | Est. Chunks | Budget Cap | Expected Found | Expected Missing |
|:-------------|:-----------|:-----------|:--------------|:----------------|
| **10 min**   | 3          | 30         | 3 (all)       | 0               |
| **30 min**   | 8          | 30         | ~8            | ~0              |
| **40 min**   | 10         | 30         | ~9-10         | 0-1             |
| **1 hour**   | 15         | 45         | ~14           | ~1              |
| **2 hours**  | 30         | 90 → 100 (cap) | ~29      | ~1              |

> [!WARNING]
> **Tradeoff:** This approach may miss 1-2 chunks on unlucky runs for longer videos. A missing chunk means ~4 minutes of video is lost. This is an acceptable tradeoff vs. getting shadow-banned by the API.

---

## Edge Cases & How They're Handled

| Edge Case | How It's Handled |
|-----------|------------------|
| **Very short video (1-2 chunks)** | Min budget of 30 requests. More than enough to find 1-2 chunks and confirm no others exist. |
| **Network error during M3U8 query** | `query_random_chunk` catches `RequestException`, sleeps 2s, returns empty (loop continues). |
| **Network error on TS chunk download** | `_download_segment` retries up to **3 times** with 2s delays. |
| **Non-M3U8 response (throttled/banned)** | Sleeps 0.5s and returns empty (loop continues, budget still ticks down). |
| **All quality levels fail** | `QUALITIES` cascades: `1080 → 720 → 480 → 360`. Each failure triggers cleanup. |
| **Gaps remain after budget** | Missing indices are printed as warnings (⚠). Video is assembled from available chunks. |

---


Here is exactly how a scenario plays out when Telegram hits you with a `FloodWaitError` and the custom message queue kicks in:

### The Scenario: A Viral Moment
Suppose your bot goes viral in a large group, and 50 users all send a TeraBox link at the exact same minute. 

1. **Working Normally (Semaphore):** 
   - The bot receives 50 links almost simultaneously.
   - The Semaphore (`asyncio.Semaphore(20)`) immediately grabs the first 20 links and starts checking their cache/downloading them. The other 30 are waiting patiently in memory.
   - The 20 active pipelines all send a message back: `🔍 Checking cache for...`. They also start updating their `status.edit(...)` texts (`0%`, `10%`, etc.).

2. **The Breaking Point (`FloodWaitError` happens):**
   - Because 20 active jobs are constantly editing their status messages ("Uploading 10%", "Uploading 20%"), Telegram says: *"Whoa, you are sending too many API requests per second!"*
   - Telegram blocks the bot's API access entirely and throws a `FloodWaitError` telling it to wait **400 seconds**.

3. **The Custom Queue Kicks In (Mid-Processing):**
   - One of the active downloads [_safe_send(status.edit, "50%...")] hits the error. 
   - [_safe_send()] catches the error, sets the global cooldown (`_flood_until = now + 400s`), and goes to sleep for 400 seconds.
   - Any other active downloads trying to edit their text will also hit the error, update the cooldown, and sleep in place. **(Downloads don't cancel, they just pause their Telegram progress updates!)**

4. **New Users Arrive (The Queue at Work):**
   - With 150 seconds still left on the cooldown block, another user (User #51) pastes a new TeraBox link.
   - Instead of trying to process it, [_process_terabox()] checks [_flood_remaining()] and sees `150s` left.
   - The bot immediately shoves User #51's link into the `_flood_queue` and manages to send *one* last message (rate limits sometimes allow single critical replies):
     > *"⏳ Bot overloaded! Your request for [link] has been queued and will be processed automatically in ~150s."*

5. **The Cooldown Expires:**
   - 400 seconds finally pass. Telegram unblocks the bot.
   - The original 20 active downloads wake up from their sleep inside [_safe_send()], successfully update their status (`status.edit("80%...")`), and finish normally, sending the videos.
   
6. **The Background Worker Drains the Queue:**
   - The background task [_queue_worker()] wakes up and checks the `_flood_queue`.
   - It sees User #51's link sitting there.
   - It pulls it out, waits another 2 seconds (just to be gentle on Telegram's API so we don't instantly get blocked again), and then pushes it through the normal pipeline (`Checking cache... → Downloading... → Delivery`).
   - The user gets their video automatically without having had to type `/retry` or paste the URL a second time.


  ---

  BEFORE:
  Phase 4: bot.send_file(filepath) → reads disk + uploads bytes to Telegram
  Phase 5 fallback: bot.send_file(filepath) → reads disk AGAIN + uploads bytes AGAIN

  AFTER:
  Phase 4: _pre_upload_file(filepath) → reads disk once → InputFile handle
           _upload_to_storage(handle) → sends handle (no disk read)
  Phase 5 fallback: bot.send_file(handle) → reuses handle (no disk read, no re-upload)


  ---

  
  