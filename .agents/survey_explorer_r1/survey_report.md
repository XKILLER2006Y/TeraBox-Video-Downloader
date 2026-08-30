# Survey Report: R1 Multi-Engine Downloader & Stream Pipeline Optimization

**Date**: 2026-08-29  
**Investigator**: Survey Explorer 1 (`survey_explorer_r1`)  
**Scope**: All downloader engines, streaming modules, segment concatenation, chunk streaming, HLS remuxing, buffer allocations, filesystem copies, token resolution, and failovers across `/home/arifureta/TeraBox-Video-Downloader`.

---

## Executive Summary

The TeraBox Video Downloader codebase implements a multi-engine architecture capable of downloading and streaming media from diverse providers (TeraBox, Flare/CashSnap, Flezen, Diskwala, 9 Universal DL hosts, and Social Media via yt-dlp). The architecture features connection pooling (`network.py`), HLS windowed downloading (`stream_downloader.py`), FastTelethon parallel uploads (`fast_upload.py`), and Firestore caching (`firebase_db/cache.py`).

However, several critical architectural bottlenecks, redundant disk copies, and latent syntax/typing errors currently limit throughput and reliability:
1. **Redundant Disk Copies**: Multipart byte-range downloads write separate `.parts` files and concatenate them sequentially via `shutil.copyfileobj`, doubling disk write I/O. HLS downloads assemble `.ts` on disk before invoking `ffmpeg` to write `.mp4`, incurring redundant disk read/write cycles.
2. **Latent Code Defects & Pipeline Failures**:
   - `telegram_logic/terabox_exp.py`: Missing `TeraBoxRateLimited` import (causes `NameError` on 429 rate limit exceptions).
   - `flezenDL/flezen_dl.py` & `telegram_logic/flezen.py`: 6 critical defects including misplaced positional arguments (`progress_callback` passed into `cancel_event`), incorrect `check_size_limit` tuple unpacking, malformed `make_download_progress_cb` call, invalid argument passing to `get_video_attributes`, and `await` on synchronous database methods.
   - `telegram_logic/flare.py`: Argument mismatch in `record_history` (5 args passed to 4-arg function) and `stats_ok` missing size argument.
   - `firebase_db/cache.py`: Cache routing lacks branches for `"flare"` and `"flezen"`, defaulting to `"exphd"` and causing 100% cache misses.
3. **Test Infrastructure Obstacles**: Test script files (`test_features2.py`, `test_new_features.py`, `test_e2e.py`) contain top-level `sys.exit()` calls that abort pytest collection.

Below is the complete architectural audit, analysis of streaming/buffer pipelines, token resolution mechanisms, identified defects, and concrete optimization recommendations.

---

## 1. Architecture of Downloader Engines

The system contains 6 primary engine families:

```
                                    ┌───────────────────────┐
                                    │    Telegram Bot /     │
                                    │   Command Handlers    │
                                    └───────────┬───────────┘
                                                │
       ┌──────────────────┬─────────────────────┼─────────────────────┬──────────────────┐
       ▼                  ▼                     ▼                     ▼                  ▼
┌──────────────┐   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐   ┌──────────────┐
│  teraboxDL   │   │   flareDL    │      │   flezenDL   │      │  diskwalaDL  │   │ universalDL  │
├──────────────┤   ├──────────────┤      ├──────────────┤      ├──────────────┤   ├──────────────┤
│- Cookie Pool │   │- H5 API      │      │- Web Scraper │      │- Mini-App    │   │- Router      │
│- SURL Parser │   │- AES-CBC Dec │      │- Account     │      │  InitData    │   │- 9 Platforms │
│- HLS Poll    │   │- Stream URL  │      │  Auto-Save   │      │- AES-GCM Dec │   │- XFileSharing│
│- Multipart   │   └──────┬───────┘      └──────┬───────┘      │- Proxy Fall  │   └──────┬───────┘
└──────┬───────┘          │                     │              └──────┬───────┘          │
       │                  │                     │                     │                  │
       └──────────────────┴──────────┬──────────┴─────────────────────┘                  │
                                     ▼                                                   ▼
                         ┌───────────────────────┐                               ┌──────────────┐
                         │  stream_downloader    │                               │ Direct HTTP  │
                         │ - Rolling Window HLS  │                               │ Stream (1MB) │
                         │ - FFmpeg Remux (fast) │                               └──────────────┘
                         └───────────────────────┘
```

### 1.1. `teraboxDL` (`teraboxDL/`)
- **Files**: `terabox_dl.py`, `stream_downloader.py`, `public_api.py`, `errors.py`.
- **Token & Session Management**:
  - `_CookiePool`: Round-robin over `COOKIES1`..`COOKIES10` with thread-safe pointer arithmetic and live validation against `https://dm.1024tera.com/api/user/info`.
  - In-memory validation cache (`_cookie_cache`): 5-minute TTL, max 50 entries.
  - Fail-open strategy: Network errors during validation mark cookies as `"unknown"` to prevent transient errors from poisoning the pool.
  - Rate-limit invalidation: HTTP 429, 403, and errno -10 trigger `cookie_pool.invalidate()`, automatically advancing to the next cookie.
- **Metadata & Manifest Discovery**:
  - `_get_js_token`: Extracts token from share page HTML (`fn%28%22...%22%29` or `eval(decodeURIComponent(...))`).
  - `_get_share_info`: Queries `/api/shorturlinfo`.
  - `_probe_quality`: Single cheap probe before full discovery to test quality availability.
  - `_discover_all_hls_chunks`: Polls `/share/streaming` (up to 100 attempts / 2 min deadline) with request collapsing (TTL 0.5s) and `_DISCOVERY_STREAK = 4`. Returns inline `#EXTM3U` text in memory.
  - Quality descent: `M3U8_AUTO_1080` -> `M3U8_AUTO_720` -> `M3U8_AUTO_480`.
- **Download Pipeline**:
  - If streaming manifest: `stream_downloader.download_from_stream_url`.
  - If direct file: `_check_range_support` checks `Accept-Ranges: bytes`. If supported, `_download_video_multipart` downloads 4 concurrent byte-range parts into `.parts/part_i`, then stitches them together with `shutil.copyfileobj`. If unsupported, falls back to single-stream chunked download.

### 1.2. `flareDL` (`flareDL/`)
- **Files**: `flare_dl.py`, `public_api.py`, `errors.py`.
- **Token Resolution & Decryption**:
  - Matches URLs for `flareobhx.com`, `hugeboxstack.com`, `cshsnpcwio.com`, etc.
  - POST `https://api.cshsnpcwio.com/v1/h5_open_data` to fetch file metadata.
  - POST `https://api.cshsnpcwio.com/v1/h5/download_file_url` to get encrypted stream token.
  - `decrypt_flare_stream_url`: Uses AES-256-CBC with static `AES_KEY = b"CMrhmcd9oFUjWBBleiMfS0BiBfupaVsG"` and `AES_IV = b"2Xk4dLo38c9Z2Q2a"` to decrypt the stream URL.
- **Download Pipeline**:
  - Passes decrypted URL to `teraboxDL.stream_downloader.download_from_stream_url`.

### 1.3. `flezenDL` (`flezenDL/`)
- **Files**: `flezen_dl.py`, `public_api.py`, `errors.py`.
- **Token & Stream Resolution**:
  - Scrapes `https://flezen.com/s/<share_id>` for title (`<h1>` or CSS class), file size (`data-bytes`), upload date (`data-datetime`), and views.
  - Authenticated session with `FLEZEN_COOKIE` invokes `_try_save_and_resolve_stream`: calls `/user/save?id=<share_id>` and scrapes stream link from `/user/files`.
- **Download Pipeline**:
  - Calls `stream_downloader.download_from_stream_url`.

### 1.4. `diskwalaDL` (`diskwalaDL/`)
- **Files**: `diskwala_dl.py`, `public_api.py`, `errors.py`.
- **Two-Tier Resolution Strategy**:
  - **Tier 1 (Direct Telethon Mini App)**:
    - Dedicated background thread with persistent asyncio loop (`_get_auth_loop`) avoids Telethon event loop rebinding errors.
    - Mini App token caching (`_token_cache`): Bearer `initData` token cached for 1 hour.
    - POST `https://api2.diskwala.net/api/diskwala/download/d`. If response contains `_x: true`, decrypts payload via AES-256-GCM (`_DISKWALA_AES_KEY_HEX`).
    - Polls `/api/diskwala/status?link=` with adaptive backoff (0.5s -> 2.0s).
  - **Tier 2 (Scraper Proxy Fallback)**:
    - If `SESSION` is unset or direct resolution fails with `DiskwalaDirectError`, falls back to POST `<DISKWALA_PROXY_URL>` with `x-api-key`.
- **Download Pipeline**:
  - Calls `download_terabox_file_experimental`.

### 1.5. `universalDL` (`universalDL/` + standalone packages)
- **Files**: `universalDL/__init__.py`, `telegram_logic/universal.py`, and individual engines:
  - `filesaddaDL/filesadda_dl.py`: XFileSharing clone protocol (hidden input extraction, `op=download2` timer handling capped at 60s, `op=download3` final link extraction, bypass for direct links).
  - `gofileDL/gofile_dl.py`: API-based resolution (`/servers` -> `/contents/<id>`).
  - `streamtapeDL/streamtape_dl.py`: Obfuscated JS extraction (`/get_video?id=...`), SSRF protection against unauthorized host redirects.
  - `doodstreamDL/doodstream_dl.py`: Token & `/pass_md5/` extraction, dynamic referer construction.
  - `mixdropDL/mixdrop_dl.py`: Packed JS deobfuscation (`atob` and char code unpacking).
  - `streamwishDL/streamwish_dl.py`: Obfuscated JS / `/pass_md5/` token extraction.
  - `filelionsDL/filelions_dl.py`: HTML & script source regex extraction.
  - `catboxDL/catbox_dl.py`: Direct HEAD probing for Content-Length and filename.
  - `mediafireDL/mediafire_dl.py`: Scrapes direct CDN link (`download*.mediafire.com`).
- **Download Pipeline**:
  - `telegram_logic/universal.py:_download_file`: Direct streaming GET with 1MB chunk size, retry backoff (3 attempts), cancellation checking.

### 1.6. `social_dl` (`telegram_logic/social_dl.py`)
- **Engine**: Powered by `yt-dlp` in `download_social_video_sync`.
- **Capabilities**:
  - Format selection prefers MP4/H264 up to 1080p with M4A audio merged into MP4 container.
  - yt-dlp progress hooks report byte-level progress.
  - Video metadata extraction via ffprobe (`extract_video_metadata`) and thumbnail extraction via ffmpeg (`generate_video_thumbnail`).
  - Fast chunked upload via `upload_file_fast` (`fast_upload.py`).

---

## 2. Deep Dive: Streaming Pipelines, Segment Concatenation & HLS Remuxing

### 2.1. HLS Chunk Discovery & Manifest Generation
In `teraboxDL/terabox_dl.py`:
- `_discover_all_hls_chunks` dynamically discovers all segment chunks via `/share/streaming`.
- Rather than writing a temporary `.m3u8` playlist file to disk, it constructs the `#EXTM3U` manifest directly in memory as a string.
- `is_streaming_manifest` in `stream_downloader.py` checks `url.lstrip().startswith("#EXTM3U")`, enabling inline manifests without any temporary manifest file on disk.

### 2.2. Rolling Window Segment Download
In `teraboxDL/stream_downloader.py:_download_hls_from_manifest`:
- A thread pool of `HLS_PARALLEL_SEGMENTS = 4` workers downloads segments concurrently.
- `HLS_SUBMIT_WINDOW = 12` ensures only up to 12 segments are in flight ahead of the consumption point.
- **Assembly Loop**:
  ```python
  with open(ts_output, "wb") as out:
      for i in range(num_segments):
          fut = pending.pop(i)
          fut.result()  # re-raise segment failure
          with open(part_paths[i], "rb") as p:
              shutil.copyfileobj(p, out)
          os.remove(part_paths[i])
  ```
- **Analysis**:
  - Segment temporary files are removed immediately upon appending to `ts_output`. Peak disk usage for segment parts is bounded to at most 12 segments (~20–30 MB).
  - However, the assembled `.ts` file (`ts_output`) resides on disk in its entirety before remuxing.

### 2.3. HLS Remuxing (TS -> MP4 Container)
In `teraboxDL/stream_downloader.py:_remux_ts_to_mp4`:
- Invokes `ffmpeg -y -i ts_output -c copy -bsf:a aac_adtstoasc -movflags +faststart mp4_path`.
- Stream copying (`-c copy`) avoids CPU-heavy re-encoding.
- `-movflags +faststart` shifts the MP4 index (`moov` atom) to the front for streaming playback.
- Upon success, `ts_output` is deleted.
- **I/O Redundancy Analysis**:
  - Step 1: Segments downloaded and appended to `video.mp4.ts` on disk (Write: 100% of video size).
  - Step 2: FFmpeg reads `video.mp4.ts` from disk (Read: 100% of video size).
  - Step 3: FFmpeg writes `video.mp4` to disk (Write: 100% of video size).
  - Step 4: `video.mp4.ts` deleted.
  - **Total Disk I/O**: 200% write + 100% read. Peak disk storage = 200% of video size.

---

## 3. Buffer Allocation & Zero-Copy Optimization Analysis

### 3.1. Multipart Range Downloader (`teraboxDL/public_api.py`)
- **Current Implementation**:
  - `_download_video_multipart` splits the file into 4 parts (`ranges = [(start, end), ...]`).
  - Downloads each part into `download_path + ".parts/part_0..3"`.
  - Once all 4 parts complete:
    ```python
    with open(download_path, "wb") as out:
        for part_path in part_paths:
            with open(part_path, "rb") as p:
                shutil.copyfileobj(p, out)
    ```
- **Bottleneck**:
  - Writes the full file to `.parts/`, then reads and writes it again to `download_path`.
  - For a 2GB file, writes 4GB and reads 2GB.
- **Zero-Copy Optimization Proposal**:
  - Pre-allocate the output file: `with open(download_path, "wb") as f: f.truncate(total_size)`.
  - Each thread writes directly into `download_path` at its offset using `os.pwrite(fd, chunk, offset)` or a dedicated file descriptor positioned at `byte_start`.
  - Once all 4 workers finish, the file is 100% complete and ready on disk with **zero concatenation copy**.
  - **Savings**: 50% reduction in disk write operations, 100% reduction in stitch read operations, 50% reduction in peak disk footprint.

### 3.2. Direct Pipe Streaming to FFmpeg for HLS
- **Zero-Copy Remuxing Proposal**:
  - When ffmpeg is available, spawn `ffmpeg -y -i pipe:0 -c copy -bsf:a aac_adtstoasc -movflags +faststart output.mp4` with `stdin=subprocess.PIPE`.
  - As segments finish in sequential order, write segment bytes directly into `ffmpeg.stdin`.
  - Close `stdin` when all segments are sent and wait for ffmpeg to exit.
  - **Savings**: Completely eliminates the intermediate `.ts` file on disk! Eliminates 1 full file write + 1 full file read, halving disk wear and peak storage requirement.

### 3.3. FastTelethon Upload Pipeline (`telegram_logic/fast_upload.py`)
- **Analysis**:
  - Uses `MAX_PARALLEL = 4` worker tasks with `CHUNK_SIZE = 512KB`.
  - `_read_chunk` uses `os.pread(f.fileno(), size, offset)`: atomic, thread-safe, non-mutating OS read. Multiple parallel uploaders read from different offsets of the same open file without seeking or thread-safety locks.
  - Memory footprint is strictly bounded to `4 * 512KB = 2MB` RAM during uploads regardless of whether the video is 100MB or 2GB.
  - Excellent memory efficiency.

---

## 4. Token Resolution, Headers, and Mirror Failovers

### 4.1. HTTP Connection Pooling & Socket Reuse (`network.py`)
- Singleton `requests.Session` wrapped with `_TCPAdapter`.
- `TCP_NODELAY = True` disables Nagle's algorithm for sub-millisecond API request dispatch.
- `_RECV_BUFFER = 65536` (64KB socket receive buffer) optimized for bulk CDN transfer.
- `_dns_cache`: Thread-safe in-memory DNS cache with 10-minute TTL avoids repeated `getaddrinfo` syscalls across concurrent requests.
- `prewarm_connections()` pre-resolves DNS and establishes TLS sessions to critical hosts (`dm.1024tera.com`, `flareobhx.com`, `flezen.com`).
- Brotli gate (`_ACCEPT_ENCODING`): Only advertises `br` if `brotli` is importable, preventing unreadable compressed binary payloads.

### 4.2. Failover & Resilience Matrix

| Engine | Primary Resolution | Fallback / Failover Mechanism | Health Monitoring |
|---|---|---|---|
| **TeraBox** | Direct jsToken + `/api/shorturlinfo` | Cookie rotation across `COOKIES1..10`; Quality tier descent `1080 -> 720 -> 480` | `cookie_pool_health()` exposed to `/status` & dashboard |
| **Flare** | H5 Open Data + Download URL AES decrypt | Static key/IV fallback; link error classification | Exception mapping in `flareDL.errors` |
| **Flezen** | Public share page HTML parser | Account cookie (`FLEZEN_COOKIE`) auto-save to `/user/files` | Error mapping (`FlezenDirectError`) |
| **Diskwala** | Telethon Mini App Bearer token + AES-GCM decrypt | Scraper proxy (`DISKWALA_PROXY_URL`) via `x-api-key` | Token TTL caching (1 hr), auto-invalidation on 401/403 |
| **Universal** | Router matching 9 distinct platforms | Per-platform regex matchers with uniform exception trapping | Standardized `UniversalDL` exception |
| **Social** | `yt-dlp` format selection | Best MP4 fallback | Direct exception trapping |

---

## 5. Identified Bugs, Bottlenecks & Code Defects

### 5.1. Critical Defects & Pipeline Breaks

| # | File & Location | Description & Root Cause | Impact |
|---|---|---|---|
| **1** | `telegram_logic/terabox_exp.py:21` | `TeraBoxRateLimited` is caught at line 193 (`except TeraBoxRateLimited as e:`) but is **NOT imported** in the `from teraboxDL.errors import ...` statement on line 21. | When a 429 rate limit is encountered, Python throws `NameError: name 'TeraBoxRateLimited' is not defined` instead of cleanly rotating cookies. |
| **2** | `flezenDL/flezen_dl.py:184` | `download_from_stream_url(download_url, output_path, progress_callback)` passes `progress_callback` as 3rd positional argument. However, `download_from_stream_url` definition is `(stream_url, output_file, cancel_event=None, progress_callback=None)`. | The progress callback function is passed to `cancel_event`. `cancel_event.is_set()` either throws an exception or fails, and no download progress is reported. |
| **3** | `telegram_logic/flezen.py:149` | Calls `ok, reason = check_size_limit(size_bytes, is_admin)`. But `check_size_limit` takes only 1 argument (`size_bytes`) and returns `str | None`. | Unpacking `ok, reason` crashes with `TypeError: cannot unpack non-iterable NoneType object` or `ValueError`. |
| **4** | `telegram_logic/flezen.py:176, 208` | Calls `make_download_progress_cb(status, filename, size_bytes, cancel_event)` and `make_upload_progress_cb(status, filename, size_bytes, cancel_event)`. Signatures in `progress_callbacks.py` require `(status_msg, filename, size_str, loop, cancel_btn=None, ...)`. | Crashes with `TypeError: ... takes from 4 to 6 positional arguments but 4 were given` with mismatched types. |
| **5** | `telegram_logic/flezen.py:118, 119, 120, 132, 137, 152, 168, 191, 248, 251, 252, 253, 258` | Multiple synchronous functions (`stats_ok`, `stats_fail`, `record_history`, `bump_today`, `add_to_cache`) are called with `await` (e.g. `await stats_ok(...)`). | Crashes with `TypeError: object NoneType can't be used in 'await' expression`. |
| **6** | `telegram_logic/flezen.py:204` | `get_video_attributes(downloaded_path, meta, filename)` passes `filename` (str) as 3rd argument `width`. | `get_video_attributes` attempts `int(width)` on `filename` and crashes with `ValueError: invalid literal for int()`. |
| **7** | `telegram_logic/flare.py:291, 295` | Line 291 calls `stats_ok()` with no argument (should be `stats_ok(actual_size)`). Line 295 calls `record_history(chat_id, link_id, user_mode, filename, actual_size)` with 5 arguments (should be 4: `chat_id, filename, link_id, actual_size`). | Download byte counts not tracked in stats; `record_history` fails or records corrupted fields. |
| **8** | `firebase_db/cache.py:103-111` | `search_in_cache(surl, user_mode)` only checks `"exp"`, `"dw"`, and `"dl"`. If `user_mode` is `"flare"` or `"flezen"`, it falls into `else: search_order = ["exphd"]`. | Cache lookup for Flare and Flezen videos always checks the wrong bucket (`"exphd"`), resulting in 100% cache misses. |
| **9** | `tests/test_features2.py`, `tests/test_new_features.py`, `tests/test_features2_appendix.py`, `test_e2e.py` | Top-level code execution with `sys.exit()` calls. | When `pytest` discovers or imports these test files, `sys.exit()` terminates the pytest test suite immediately with exit code 3 (`INTERNALERROR`). |

---

## 6. Recommendations & Optimization Roadmap

### R1.1: Zero-Copy Multipart & HLS Streaming Refinement
1. **Direct Pwrite Multipart Downloader**:
   - Refactor `teraboxDL/public_api.py:_download_video_multipart` to pre-truncate the output file and write each chunk directly via `os.pwrite(fd, chunk, current_offset)` or pre-seeked file handles. Remove `.parts` subfolder creation and sequential `shutil.copyfileobj` stitching.
2. **Piped Remuxing in HLS Downloader**:
   - Refactor `teraboxDL/stream_downloader.py:_download_hls_from_manifest` to stream TS chunks into `ffmpeg -i pipe:0` whenever ffmpeg is present. Eliminates intermediate `.ts` file generation.
3. **Keyword-Safe Keyword Passing in `flezen_dl.py`**:
   - Pass `cancel_event=cancel_event, progress_callback=progress_callback` explicitly as keyword arguments.

### R1.2: Hardening Telegram Pipelines
1. **Fix `telegram_logic/terabox_exp.py`**:
   - Add `TeraBoxRateLimited` to the `from teraboxDL.errors import ...` import.
2. **Fix `telegram_logic/flezen.py`**:
   - Align with `terabox_exp.py` / `flare.py` patterns:
     - Use `check_size_limit(size_bytes)` directly.
     - Pass `(status, filename, size_str, loop, cancel_btn, expected_total=size_bytes)` to `make_download_progress_cb`.
     - Remove `await` from synchronous `firebase_db` calls (`asyncio.to_thread(record_history, chat_id, filename, link_id, actual_size)`).
     - Fix `get_video_attributes(filepath, duration=meta.get("duration"), width=meta.get("width"), height=meta.get("height"))`.
     - Fix `add_to_cache(link_id, storage_msg.id, user_mode)` call.
3. **Fix `telegram_logic/flare.py`**:
   - Pass `stats_ok(actual_size)`.
   - Fix `record_history(chat_id, filename, link_id, actual_size)` argument order.
4. **Fix `firebase_db/cache.py`**:
   - Add `"flare"` and `"flezen"` to `MODE` Literal, `_BUCKETS` tuple, and `search_in_cache` router.

### R1.3: Test Infrastructure Standardization
1. Wrap top-level script logic in `test_features2.py`, `test_new_features.py`, `test_features2_appendix.py`, and `test_e2e.py` with `if __name__ == "__main__":` blocks or convert them to standard pytest test cases so `pytest tests/` runs cleanly without `SystemExit` collection crashes.
