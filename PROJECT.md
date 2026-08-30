# Project: Comprehensive System-Wide Optimization of TeraBox Video Downloader Bot

## Architecture
The TeraBox Video Downloader Bot is an asynchronous Telegram bot and web service built on Python, Telethon, FastAPI, and modular downloader engines.

### Data Flow & Subsystems
1. **User Interaction & Command Routing (`telegram_logic/commands/`, `telegram_logic/handlers/`)**:
   - Telegram message events dispatched to command handlers (`/start`, `/dl`, `/exp`, `/exphd`, `/dw`, `/mp3`, `/random`, `/settings`, `/status`, `/stats`, `/quota`, `/history`, `/op`, `/recent`, `/broadcast`).
   - Deep link extractors identify URL target (TeraBox, Diskwala, Flare, Flezen, YouTube/Social, GoFile).
2. **Download Engines (`teraboxDL/`, `flareDL/`, `flezenDL/`, `diskwalaDL/`, `universalDL/`, `social_dl/`)**:
   - Resolve direct streaming / chunk URLs and file metadata.
   - Stream segments / chunks using multi-part concurrency or HLS remuxing into local downloads.
   - Zero-copy direct disk writing without redundant `.parts` staging or intermediate `.ts` file re-reads.
3. **Network & Connection Layer (`network.py`)**:
   - Centralized HTTP session pooling, TCP_NODELAY socket options, and thread-safe DNS caching.
4. **Telegram Fast Upload (`telegram_logic/fast_upload.py`)**:
   - Bounded concurrency worker pool uploading 512KB chunks directly to Telegram datacenters.
   - Monotonic progress tracking and automatic chunk retries.
5. **Storage & Active File Management (`main.py`, `storage/`)**:
   - Centralized active transfer file registry (`_active_disk_paths`) preventing GC race conditions during transfers >10 minutes.
   - Automated orphan cleanup for `.parts`, `.ts`, and temp files.
6. **Persistence & Cache (`firebase_db/`)**:
   - Metadata and URL caching across all engine types (`get`, `exp`, `exphd`, `dw`, `dl`, `social`, `flare`, `flezen`).
7. **Web Server & Health Endpoints (`main.py`)**:
   - FastAPI lifespan server serving `/health`, `/ping`, `/dash`, and `/api/stats`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Multi-Part Zero-Copy Download | Direct `pwrite` chunk writing to destination file without `.parts` duplication | M1 | Survey R1 |
| 2 | HLS Remuxing Stream Pipeline | Direct piped remuxing (`ffmpeg -i pipe:0`) or optimized streaming for TS segments | M1 | Survey R1 |
| 3 | Downloader Engine Hardening | Fix imports, argument bindings, and error handlers across all 6 downloader engines | M1 | Survey R1 |
| 4 | Engine Cache Buckets Expansion | Support `social`, `flare`, and `flezen` buckets in Firestore cache lookup & storage | M1 | Survey R1 |
| 5 | Thread-Safe DNS Resolver Cache | Safe getaddrinfo caching with TTL and preserved original socket resolver | M2 | Survey R2 |
| 6 | Unified Network Connection Pooling | Route all engine HTTP requests through pooled `network.py` sessions | M2 | Survey R2 |
| 7 | Bounded FastTelethon Uploader | Bounded worker pool (8-16 workers), monotonic progress callback, and chunk retry | M2 | Survey R2 |
| 8 | Safe Storage GC & Orphan Cleanup | Active transfer tracking preventing GC file deletion while aggressively purging orphans | M2 | Survey R2 |
| 9 | Command Handlers Runtime Fixes | Resolve signature mismatches, sync/async `await` traps in `flezen.py`, `flare.py`, `terabox_exp.py` | M3 | Survey R3 |
| 10 | Universal Deep-Link Routing | Expand `/start <url>` and `/mp3 <url>` to handle all engine types with graceful fallback | M3 | Survey R3 |
| 11 | Unified Task Cancellation | Standardize task key naming across all engines and commands for `/cancel` support | M3 | Survey R3 |
| 12 | Error Message Hardening | User-facing resilient feedback for expired, rate-limited, or invalid links | M3 | Survey R3 |
| 13 | Pytest Test Discovery & Runner Fix | Eliminate top-level `sys.exit()` blockers and configure clean pytest test collection | M4 / Test | Survey R3 |
| 14 | Comprehensive E2E & Unit Test Suite | 100% test pass rate across Tiers 1-4 (all engines, commands, uploads, health endpoints) | M4 / Test | Survey R3 |
| 15 | Adversarial Coverage Hardening | White-box edge case testing and gap analysis (Tier 5) | Final M | Survey R3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Downloader Engines & Zero-Copy Streaming | R1: `teraboxDL`, `flareDL`, `flezenDL`, `diskwalaDL`, `universalDL`, `social_dl`, zero-copy pwrite, piped HLS remux, cache buckets | none | PLANNED |
| M2 | Memory, Connection Pooling & Storage GC | R2: `network.py`, `fast_upload.py`, active transfer registry, storage GC loop in `main.py` | none | PLANNED |
| M3 | Bot Command Hardening & Handler Integrity | R3: `telegram_logic/commands/`, `terabox_exp.py`, `flare.py`, `flezen.py`, `/cancel` keys, error messages, static analysis | M1, M2 | PLANNED |
| M4 / Test | Test Suite Normalization & E2E Testing Suite | Pytest discovery fixes, test infrastructure, Tiers 1-4 comprehensive test suite, `TEST_READY.md` | M1, M2, M3 | PLANNED |
| Final M | 100% E2E Pass & Adversarial Hardening | Pass 100% of E2E test suite + Tier 5 white-box adversarial stress testing | M4 / Test | PLANNED |

## Interface Contracts

### 1. Zero-Copy Downloader Contract (`teraboxDL/public_api.py`, `teraboxDL/stream_downloader.py`)
- `download_video_multipart(url: str, output_path: str, size: int, headers: dict, cancel_event: Event, progress_cb: Callable) -> str`:
  - Directly allocates destination file on disk and uses `os.pwrite` with chunk offsets.
  - Zero `.parts` directory creation or secondary `shutil.copyfileobj` pass.
- `download_from_stream_url(stream_url: str, output_file: str, cancel_event: Event | None = None, progress_callback: Callable | None = None) -> str`:
  - Consistent signature taking `cancel_event` and `progress_callback`.

### 2. Active File Transfer Registry Contract (`main.py`, `storage/`, `telegram_logic/`)
- `mark_file_active(filepath: str)`: Registers file as actively downloading or uploading.
- `unmark_file_active(filepath: str)`: Unregisters file upon completion or failure.
- `is_file_active(filepath: str) -> bool`: Returns `True` if file is actively in transfer.
- `_storage_cleanup_loop`: Only deletes files older than threshold where `is_file_active(f) is False`.

### 3. Fast Upload Contract (`telegram_logic/fast_upload.py`)
- `upload_file_fast(client, file_path: str, progress_callback: Callable = None, cancel_event: Event = None, max_workers: int = 12) -> InputFile`:
  - Guarantees strictly monotonic `bytes_sent` reporting to `progress_callback(bytes_sent, total_bytes)`.
  - Max concurrent worker tasks bounded to `max_workers` (no task explosion on multi-GB files).
  - Retries failed chunk uploads up to 3 times on transient DC errors.

### 4. Bot Command Handlers & Database Helper Contract
- Database helpers in `firebase_db`: Synchronous functions (`stats_ok`, `stats_fail`, `record_history`, `bump_today`, `add_to_cache`). Must be called synchronously without `await`, or wrapped in `asyncio.to_thread` if offloading to thread.
- `make_download_progress_cb(status_msg, filename, size_str, loop, cancel_btn=None, expected_total=0)`
- `make_upload_progress_cb(status_msg, filename, size_str, loop, cancel_btn=None)`

## Code Layout
- `teraboxDL/`: TeraBox scraper, multi-part downloader, HLS stream downloader, error definitions.
- `flareDL/`: Flare downloader client, link resolution, session management.
- `flezenDL/`: Flezen downloader client, token extraction, stream routing.
- `diskwalaDL/`: Diskwala scraper and proxy integration.
- `universalDL/`: Universal extractor (YouTube, GoFile, direct links).
- `social_dl/`: Social media downloader (Instagram, TikTok, Twitter/X).
- `network.py`: DNS caching, persistent connection pools, HTTP retry adapters.
- `telegram_logic/`:
  - `commands/`: All 15 Telegram bot command modules.
  - `fast_upload.py`: High-speed DC parallel chunk uploader.
  - `queue.py`: Telegram flood-wait message queue (`MessageQueue`).
  - `flare.py`, `flezen.py`, `terabox_exp.py`, `diskwala.py`, `universal.py`, `social_dl.py`: Engine workflow orchestrators.
- `firebase_db/`: Firestore caching, quota tracking, stats recording.
- `main.py`: Bot runner, storage cleanup background loop, FastAPI server (`/health`, `/ping`, `/api/stats`).
- `tests/`: Unit, integration, and E2E test suites.
