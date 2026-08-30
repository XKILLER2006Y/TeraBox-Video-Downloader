# Comprehensive Survey Report: R3 — Static Analysis, Error Hardening & Test Suite

**Survey Explorer**: Survey Explorer 3  
**Target Codebase**: `/home/arifureta/TeraBox-Video-Downloader`  
**Focus Area**: R3 — Static Analysis, Error Hardening & Comprehensive Test Suite  
**Date**: 2026-08-30  

---

## Executive Summary

This report provides an in-depth static analysis, architecture audit, error hardening investigation, command routing review, and test suite evaluation for the **TeraBox Video Downloader** system.

### Key Highlights
1. **Command Architecture (15 Bot Commands)**: All 15 commands and callback handlers were traced from entry point to execution. Several commands lack multi-platform support (e.g. `/start <url>` and `/mp3 <url>` only accept TeraBox links) or fail silently on unconfigured storage (`/random`).
2. **Latent Code Defects & Runtime Traps**:
   - Critical missing imports and undefined names (`TeraBoxRateLimited`, `alerts` in `terabox_exp.py`; `requests` in `diskwala_dl.py`).
   - Severe signature mismatches and sync/async call errors in `flezen.py` and `flare.py` that cause unhandled runtime exceptions during delivery and stats tracking.
   - Non-existent method call `terabox_queue.enqueue()` in `flare.py`.
   - `firebase_db/cache.py` missing cache buckets for `social`, `flare`, and `flezen`.
3. **Test Suite Status & Pytest Collection Blockers**:
   - Pytest execution crashes during test collection due to un-guarded procedural test scripts (`test_e2e.py`, `tests/test_features2.py`, `tests/test_features2_appendix.py`, `tests/test_new_features.py`) calling `sys.exit()` at module level.
   - Diagnostic network tools (`test_flaresolver.py`, `test_streaming_diag.py`) are named with the `test_*.py` pattern, polluting automated test discovery.
   - Existing unit tests (`test_enhancements.py`, `test_flare.py`, `test_flezen.py`, `test_social_media.py`) pass (16/16) when isolated, but lack coverage for commands, web endpoints, and engine failover.
4. **Web Server & Health Endpoints**:
   - `/health`, `/ping`, `/dash`, and `/api/stats` are well-structured in FastAPI lifespan, but require test harness validation and proper token protection verification.

---

## 1. Telegram Bot Commands Audit (All 15 Commands)

The bot exposes 15 Telegram commands across `telegram_logic/commands/` and `main.py`, wired via `@bot.on` decorators imported in `telegram_logic/commands/__init__.py`.

| # | Command | Source Module | Handler Function | User Scope | Functionality & Flow |
|---|---|---|---|---|---|
| 1 | `/start` | `telegram_logic/commands/start.py:28` | `cmd_start` | Public | Displays welcome message. If deep-link argument provided (`/start <link>`), parses link and initiates immediate download. |
| 2 | `/dl` | `telegram_logic/commands/universal.py:36` | `handle_dl` | Public | Universal downloader entry point. Parses flags (`[quality]`, `comp`), auto-detects platform across all 6 engine families, falls back to universal HTTP/HLS. |
| 3 | `/exp` | `telegram_logic/commands/experimental.py:27` | `cmd_get_exp` | Public | Explicit fast streaming downloader for TeraBox links with quality & compression flag support. |
| 4 | `/exphd` | `telegram_logic/commands/experimental.py:58` | `cmd_get_exp_hd` | Public | Explicit 1080p HD downloader for TeraBox links. |
| 5 | `/dw` | `telegram_logic/commands/diskwala.py:11` | `cmd_dw` | Public | Explicit Diskwala video downloader. Provides hint if TeraBox link is passed. |
| 6 | `/mp3` | `telegram_logic/commands/mp3.py:84` | `cmd_mp3` | Public | Extracts audio from video as MP3 with bitrate options (128/192/320 kbps) via FFmpeg. |
| 7 | `/random` | `telegram_logic/commands/random.py:12` | `cmd_random` | Public | Fetches and sends a random cached video from `STORAGE_GROUP_ID` using Firestore cache index. |
| 8 | `/settings`| `telegram_logic/commands/settings.py:10` | `cmd_settings` | Public | Interactive settings menu showing user chat info and inline keyboard buttons to toggle default download mode. |
| 9 | `/status` | `telegram_logic/commands/status.py:116` | `cmd_status` | Public / Admin | Bot health dashboard (uptime, active tasks, queue depth, RAM RSS, storage size). Admin sees cookie pool status & rate-limit details. |
| 10 | `/stats` | `telegram_logic/commands/stats.py:76` | `cmd_stats` | Public / Admin | User-specific statistics (total downloads, data delivered, recent downloads count, default mode). Admin sees global totals. |
| 11 | `/quota` | `telegram_logic/commands/stats.py:99` | `cmd_quota` | Public | Visual ASCII progress bar and count of daily downloads used vs `DAILY_LIMIT_PER_USER`. |
| 12 | `/history`| `telegram_logic/commands/history.py:15` | `cmd_history` | Public | Displays user's recent download history (last 10-20 items) with timestamps. |
| 13 | `/op` | `telegram_logic/commands/opinion.py:12` | `cmd_opinion` | Public | Submits feedback/reports directly to `ADMIN_ID`. Protected by length cap (1000 chars) and rate limiter. |
| 14 | `/recent` | `telegram_logic/commands/recent.py:14` | `cmd_recent` | Admin Only | Lists Top 7 most recent active users, last active timestamp (IST), user IDs, and usernames. |
| 15 | `/broadcast`| `telegram_logic/commands/broadcast.py:47` | `cmd_broadcast` | Admin Only | Broadcasts text message or replied media (photo, video, document, audio, sticker) to all tracked users with flood control. |

### Additional Interactive Callbacks & Handlers
- **Callback `setmode_(.*)`** (`settings.py:62`): Whitelist-validated mode switcher (`exp`, `exphd`, `dw`).
- **Callback `^(?:u)?cancel:`** (`cancel_download.py:17`): Cancels active download task via `threading.Event`.
- **Callback `tpick:(.+)` & `tpickall:(.+)`** (`experimental.py:103,118`): Interactive multi-file share picker for TeraBox folders.
- **Inline Query `events.InlineQuery`** (`inline.py:27`): Allows inline link dispatching from any Telegram chat.

### Command Routing & UX Vulnerabilities Identified
1. **Narrow `/start` & `/mp3` platform support**: `/start <link>` only calls `extract_all_terabox_url_exp`. If a user deep-links Diskwala, YouTube, GoFile, or Flare links, the link is ignored. Similarly, `/mp3 <link>` calls `get_video_info` directly from `teraboxDL`, rejecting all other platforms.
2. **Missing cancellation mapping for `/mp3`**: `mp3.py` registers `task_key = f"mp3-{chat_id}-{rid}"`, but `cancel_download.py` looks up tuple `(chat_id, surl)`. Thus, `/mp3` cancellation cannot be triggered via standard callback data.
3. **Silent failure in `/random` without storage group**: When `STORAGE_GROUP_ID` is unset (default `0`), `/random` answers `"⚠️ Could not retrieve random video. Try again!"` rather than explaining that video caching is disabled.

---

## 2. Latent Typing Errors, Broken Imports & Dead Code Paths

Deep static analysis using `ruff`, `mypy`, and AST verification identified multiple severe latent defects:

### Defect 1: Undefined Names in `telegram_logic/terabox_exp.py`
- **Location**: `telegram_logic/terabox_exp.py:193-195`
- **Code**:
  ```python
  except TeraBoxRateLimited as e:
      log.warning(f"TeraBox rate-limited (HTTP 429) for surl={surl}: {e}")
      alerts.dispatch(
          f"⚠️ TeraBox rate limit (429) hit for `surl={surl}`.\n"
          "Rotating cookies automatically.",
          key="tb-429",
      )
  ```
- **Root Cause**: `TeraBoxRateLimited` is not imported from `teraboxDL.errors`, and `alerts` is not imported from `telegram_logic.alerts`.
- **Impact**: Any TeraBox HTTP 429 triggers `NameError`, escaping into the generic `except Exception:` block, failing to alert the admin or notify the user of cookie rotation.

### Defect 2: Missing `requests` Import in `diskwalaDL/diskwala_dl.py`
- **Location**: `diskwalaDL/diskwala_dl.py:378, 385`
- **Code**:
  ```python
  try:
      _start_download(diskwala_url, headers)
  except requests.RequestException as e:
      raise DiskwalaDirectError(f"Network error calling Diskwala API: {e}") from e
  ```
- **Root Cause**: `import requests` is missing in `diskwalaDL/diskwala_dl.py`.
- **Impact**: When network errors occur during Diskwala Mini App API calls, Python crashes with `NameError: name 'requests' is not defined`.

### Defect 3: Severe Signature & Sync/Async Incompatibilities in `telegram_logic/flezen.py`
- **Location**: `telegram_logic/flezen.py:248-258`
- **Code**:
  ```python
  if storage_msg:
      await add_to_cache(link_id, storage_msg.id, filename, size_bytes, user_mode)

  await status.delete()
  await stats_ok(user_mode, is_cache=False, latency=total_time)
  await record_history(chat_id, link_id, filename, size_bytes, user_mode)
  await bump_today(chat_id)
  ```
- **Root Cause**:
  1. `add_to_cache` is synchronous and takes 3 arguments `(surl, msg_id, user_mode)`, called with 5 arguments and `await`.
  2. `stats_ok` (`record_success`) is synchronous and takes `(size_bytes: int = 0)`, called with invalid kwargs and `await`.
  3. `record_history` is synchronous and takes 4 arguments `(chat_id, title, key, size=0)`, called with 5 arguments and `await`.
  4. `bump_today` is synchronous, called with `await`.
  5. `stats_fail` (`record_failure`) is synchronous and takes 0 arguments, called with invalid kwargs and `await`.
- **Impact**: **100% of Flezen downloads crash on delivery/stats recording** with `TypeError` / `RuntimeError`.

### Defect 4: Method & Argument Mismatches in `telegram_logic/flare.py`
- **Location**: `telegram_logic/flare.py:55, 216, 237, 273, 295, 300`
- **Issues**:
  - Line 55: `bind_context(request_id=new_request_id(), chat_id=..., link_id=...)` -> invalid kwargs (`user_id` and `download_id` expected).
  - Line 216: `_pre_upload_file(filepath, cancel_event=..., progress_callback=...)` -> `_pre_upload_file` takes `(filepath, progress_cb=None)`.
  - Line 237: `_upload_to_storage(uploaded_file, caption=..., file_name=...)` -> invalid kwargs.
  - Line 273: `terabox_queue.enqueue(...)` -> `AttributeError: 'MessageQueue' object has no attribute 'enqueue'`.
  - Line 295: `record_history(chat_id, link_id, user_mode, filename, actual_size)` -> 5 args instead of 4.
  - Line 300: `release_user_slot(chat_id)` -> missing `is_admin` arg.

### Defect 5: Missing Cache Buckets in `firebase_db/cache.py`
- **Location**: `firebase_db/cache.py:39-41`
- **Code**:
  ```python
  MODE = Literal["get", "exp", "exphd", "dw", "dl"]
  _BUCKETS = ("get", "exp", "exphd", "dw", "dl")
  ```
- **Impact**: Does not include `"social"`, `"flare"`, `"flezen"`. Stored entries for these platforms cannot be indexed or retrieved by `/random`.

### Defect 6: Dead Code in `tests/test_features2_appendix.py`
- **Location**: `tests/test_features2_appendix.py:667-703`
- **Code**: `sys.exit(1 if FAIL else 0)` at line 667 renders lines 670-703 unreachable.

---

## 3. Existing Test Suites, Fixtures & Coverage Gaps

### Current Test Suite Inventory

| Test File | Test Runner / Paradigm | Status when Run Directly | Pytest Discovery Status | Notes |
|---|---|---|---|---|
| `tests/test_enhancements.py` | `unittest.TestCase` | PASS (5/5) | PASS | Well-structured unit tests with mocks for Diskwala token cache, regexes, FastTelethon. |
| `tests/test_flare.py` | `unittest.TestCase` | PASS (4/4) | PASS | Tests Flare ID extraction, AES decryption, regex parsing. |
| `tests/test_flezen.py` | `pytest` functions | PASS (4/4) | FAILS without `PYTHONPATH=.` | Needs `sys.path.insert` or pytest config. |
| `tests/test_social_media.py` | `unittest.TestCase` | PASS (3/3) | PASS | Tests social URL regexes and media metadata extraction with Firebase mocks. |
| `tests/test_features2.py` | Procedural script | PASS (12 checks) | CRASHES (`SystemExit`) | Top-level `sys.exit()` halts pytest collector. |
| `tests/test_features2_appendix.py` | Procedural script | PASS (35 checks) | CRASHES (`SystemExit`) | Top-level `sys.exit()` and dead code after line 667. |
| `tests/test_new_features.py` | Procedural script | PASS (28 checks) | CRASHES (`SystemExit`) | Top-level `sys.exit()` halts pytest collector. |
| `tests/test_flaresolver.py` | CLI diagnostic tool | Skipped (needs network) | POLLUTES pytest | Not a unit test — interactive network probe. |
| `tests/test_streaming_diag.py`| CLI diagnostic tool | Skipped (needs network) | POLLUTES pytest | Not a unit test — interactive network probe. |
| `test_e2e.py` | Procedural script (root)| Fails without network | CRASHES pytest | Top-level `sys.exit(1)` on line 198 crashes root test run. |
| `test_decode.py` | Standalone script (root)| PASS | Skipped | Test script for JSON decoding logic. |
| `test_bot.py` | Standalone script (root)| Skipped (needs Telegram) | Skipped | Manual bot testing script. |

### Why `pytest` Currently Exits with Code 3 / Crashes
1. When pytest traverses the repository, it discovers every file matching `test_*.py` or `*_test.py`.
2. Pytest executes the module during the collection phase.
3. `test_e2e.py`, `test_features2.py`, `test_features2_appendix.py`, and `test_new_features.py` run imperative assertions and call `sys.exit(0)` or `sys.exit(1)` immediately during module import.
4. Python raises `SystemExit`, which interrupts pytest's collection engine with `INTERNALERROR: SystemExit: 0`.

### Coverage Gaps to Remediate
1. **Command Handler Unit Tests**: Zero automated tests exist for all 15 commands (`/start`, `/dl`, `/exp`, `/exphd`, `/dw`, `/mp3`, `/random`, `/settings`, `/status`, `/stats`, `/quota`, `/history`, `/op`, `/recent`, `/broadcast`).
2. **FastAPI Web Server Endpoints**: No unit tests using `TestClient(app)` to verify `/ping`, `/health` (healthy & 503 degraded states), `/dash`, and `/api/stats` (auth token validation).
3. **Universal Engine Parsers**: Missing unit tests with mock payloads for `GoFile`, `MediaFire`, `CatBox`, `StreamTape`, `MixDrop`, `StreamWish`, `FileLions`, `FilesAdda`.
4. **Resilience & Concurrency Tests**: Lack of tests verifying `acquire_user_slot`, `release_user_slot`, `active_tasks` cleanup under cancellation, and rate-limit backoff.

---

## 4. Health Check Endpoints & Web Server Integration

The web server in `main.py` is built on FastAPI and managed via an `asynccontextmanager` lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(run_bot())
    cleanup_task = asyncio.create_task(_storage_cleanup_loop())
    mem_task = asyncio.create_task(_memory_monitor_loop())
    yield
    # Graceful shutdown:
    telegram_logic_bot.shutting_down.set()
    cleanup_task.cancel()
    leftover = await telegram_logic_bot.drain_active_tasks(timeout=90.0)
    mem_task.cancel()
    bot_task.cancel()
    if bot.is_connected():
        await bot.disconnect()
```

### Endpoints Evaluation
1. **`GET /ping`**:
   - Returns string `"pong"` with status 200.
   - Ideal for basic L4/L7 load balancer ping.
2. **`GET /health`**:
   - Evaluates:
     - `connected = bot.is_connected()`
     - `hb_age = time.time() - telegram_logic_bot.last_heartbeat`
     - `healthy = connected and hb_age < 600`
   - Returns HTTP 200 `{"status": "ok", ...}` or HTTP 503 `{"status": "degraded", ...}`.
   - `last_heartbeat` is initialized to `time.time()` in `telegram_logic/bot.py:74` and refreshed every 2m by `_storage_cleanup_loop` and 5m by `_memory_monitor_loop`.
3. **`GET /dash` & `GET /api/stats`**:
   - Secured with query parameter `?t=<DASHBOARD_TOKEN>`.
   - `/dash` returns a standalone HTML dashboard showing uptime, active downloads, flood queue size, RAM RSS, today's success/failure counts, and cookie pool health.
   - `/api/stats` returns JSON metrics consumed by the dashboard.
   - Returns HTTP 403 when `t` does not match `DASHBOARD_TOKEN`, or HTTP 404 when `DASHBOARD_TOKEN` is unset in `.env`.

---

## 5. Architectural Recommendations & Action Plan

### Phase 1: Fix All Latent Runtime Traps & Type Errors
1. **`telegram_logic/terabox_exp.py`**:
   - Import `TeraBoxRateLimited` from `teraboxDL.errors`.
   - Import `alerts` from `. import alerts`.
2. **`diskwalaDL/diskwala_dl.py`**:
   - Add `import requests`.
3. **`telegram_logic/flezen.py`**:
   - Synchronize DB calls: invoke `add_to_cache(link_id, storage_msg.id, user_mode)` without `await`, `stats_ok(size_bytes)`, `record_history(chat_id, filename, link_id, size_bytes)`, `bump_today(chat_id)`, and `stats_fail()` synchronously or via `asyncio.to_thread`.
4. **`telegram_logic/flare.py`**:
   - Fix `bind_context(request_id=..., user_id=..., download_id=...)`.
   - Fix `_pre_upload_file(filepath, progress_cb=...)`.
   - Fix `_upload_to_storage(uploaded_file, filename, thumb=..., attributes=...)`.
   - Remove invalid `terabox_queue.enqueue()` call (use `_safe_send` retry).
   - Correct `record_history` and `release_user_slot` arguments.
5. **`firebase_db/cache.py`**:
   - Extend `_BUCKETS` to include `("get", "exp", "exphd", "dw", "dl", "social", "flare", "flezen")`.
6. **`telegram_logic/commands/mp3.py`**:
   - Clean up `paths` typing in `_cleanup` helper.

### Phase 2: Refactor Test Suites for 100% Pytest Compatibility
1. **Refactor Procedural Test Scripts into Standard Test Modules**:
   - Convert `tests/test_features2.py`, `tests/test_features2_appendix.py`, and `tests/test_new_features.py` from top-level scripts into standard `pytest` test functions (`test_hls_download()`, `test_admin_alerts()`, `test_user_slots()`, `test_stats_recording()`, etc.).
   - Remove top-level `sys.exit()` calls.
2. **Isolate CLI Diagnostic Scripts**:
   - Rename `tests/test_flaresolver.py` → `scripts/diag_flaresolver.py` (or add `pytest.mark.skip`).
   - Rename `tests/test_streaming_diag.py` → `scripts/diag_streaming.py`.
   - Wrap root `test_e2e.py`, `test_decode.py`, `test_bot.py` inside `if __name__ == "__main__":` or move diagnostic scripts to `scripts/`.
3. **Add `conftest.py` & Root Pytest Config (`pytest.ini` / `pyproject.toml`)**:
   - Ensure repository root is on `pythonpath`.
   - Provide standard fixtures and mocks for Telegram client, Firebase DB, and network sessions.

### Phase 3: Implement Comprehensive Test Coverage
1. **Bot Command Handler Tests (`tests/test_commands.py`)**:
   - Test all 15 commands with mocked Telethon events (start, dl, exp, exphd, dw, mp3, random, settings, status, stats, quota, history, op, recent, broadcast).
   - Test admin-only authorization enforcement (`/recent`, `/broadcast`).
   - Test rate-limit blocks and slot limits.
2. **Web Server & Health Check Tests (`tests/test_web_server.py`)**:
   - Test `/ping` -> 200 `"pong"`.
   - Test `/health` -> 200 (healthy) and 503 (stale heartbeat / disconnected).
   - Test `/dash` & `/api/stats` -> 200 with valid token, 403 with invalid token, 404 when token unset.
3. **Downloader Engine Unit Tests (`tests/test_universal_dl.py`)**:
   - Test URL detection and mocked metadata extraction for GoFile, CatBox, MediaFire, MixDrop, StreamWish, FileLions, FilesAdda.

---
