# Survey Report: Memory Management, Storage GC & Concurrency Tuning (R2)

**Explorer**: Survey Explorer 2  
**Date**: 2026-08-30  
**Target Codebase**: `TeraBox-Video-Downloader`  
**Focus Area**: R2 — Network Requests, HTTP Sessions, FastTelethon Upload Concurrency, Storage GC & Concurrency Safety

---

## 1. Executive Summary

A comprehensive, line-by-line audit of the `TeraBox-Video-Downloader` codebase was conducted targeting network connection pooling, parallel upload pipelines, and background storage garbage collection.

### Key High-Severity Findings
1. **Broken / Inactive DNS Cache in `network.py`**: The in-memory TTL DNS cache (`_cached_getaddrinfo`) is never installed onto `socket.getaddrinfo`. If it were patched as currently coded, it would cause infinite recursion and `RecursionError` due to lack of an underlying original function handle, while also failing IPv6.
2. **Ad-Hoc Unpooled HTTP Requests Across Engines**: `flareDL/flare_dl.py` (lines 98, 149), `flezenDL/flezen_dl.py` (lines 37, 97), and `diskwalaDL/public_api.py` (line 102) instantiate raw `requests.post()` or new `requests.Session()` objects per request, bypassing the centralized `get_session()` singleton and causing TLS handshake thrashing.
3. **Task Explosion & Unhandled Chunk Failures in FastTelethon (`fast_upload.py`)**: For a 2GB file, `upload_file_fast` spawns 4,000 `asyncio.Task` objects simultaneously instead of using a bounded worker pool. Furthermore, `_send_partial` lacks any retry logic (a single transient DC glitch fails the entire upload) and progress reporting uses non-monotonic indexing (`min((file_part + 1) * CHUNK_SIZE, file_size)`), causing progress percentages to jump backwards and forwards during out-of-order chunk completion.
4. **Broken Call Signatures & Crashes in Engine Upload Handlers**:
   - `telegram_logic/flare.py:216`: Passes `cancel_event` and `progress_callback` to `_pre_upload_file`, causing `TypeError`.
   - `telegram_logic/flezen.py:213`: Passes invalid keyword arguments to `_pre_upload_file` and calls `make_upload_progress_cb` with `cancel_event` in place of the event loop (`loop`), which crashes runtime progress updates.
   - `telegram_logic/universal.py:228`: Bypasses `_pre_upload_file` and `upload_file_fast` entirely, falling back to slow single-threaded uploads.
5. **Race Condition Between Storage GC Loop and Active Transfers**: `_storage_cleanup_loop` in `main.py` deletes any file in `storage/` or `downloads/` whose `mtime` is older than 600s (10 min). For large files (e.g., 2GB taking >10 min to upload/download over slower connections), active in-flight files are purged while being read/written.
6. **2x Disk Space Amplification in Multi-Part Downloads**: `teraboxDL/public_api.py` downloads 4 segment files into `.parts/part_i`, then copies them sequentially into the output file, requiring 200% disk space (e.g., 4GB free space needed for a 2GB download).

---

## 2. Deep Dive: Network Requests, Connection Pooling & Socket Reuse

### 2.1 Current Architecture in `network.py`
`network.py` defines a global `requests.Session` singleton accessed via `get_session()`. It implements `_TCPAdapter(HTTPAdapter)` which enables `TCP_NODELAY` and sets a configurable `SO_RCVBUF` (default 64KB).

```python
# network.py:123-134
s = requests.Session()
s.headers.update(_browser_headers)
adapter = _TCPAdapter(
    pool_connections=pool_size,
    pool_maxsize=pool_size,
    max_retries=Retry(total=0),
    nodelay=True,
    recv_buffer=_RECV_BUFFER,
)
s.mount("https://", adapter)
s.mount("http://", adapter)
```

### 2.2 Critical Vulnerabilities & Defects Identified

#### A. Inactive / Broken In-Memory DNS Cache (`network.py:88-101`)
- **Observation**: `_cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0)` is defined in `network.py` lines 88-101, but is **never monkey-patched** into `socket.getaddrinfo` or `urllib3`.
- **Defect 1**: OS-level blocking `getaddrinfo` calls are executed for every request across all threads, introducing 20–150ms latency spikes per host resolution.
- **Defect 2 (Latent Recursion Bug)**: In `network.py` line 96:
  ```python
  result = socket.getaddrinfo(host, port, family, type, proto, flags)
  ```
  If a developer attempts `socket.getaddrinfo = _cached_getaddrinfo`, line 96 invokes `_cached_getaddrinfo` recursively, crashing with `RecursionError`.
- **Defect 3 (IPv6 / Family Truncation)**: Line 94 returns hardcoded `socket.AF_INET`, discarding IPv6 addresses (`AF_INET6`) and proto/flags metadata requested by callers.

#### B. Direct Unpooled `requests` in Downloader Modules
Several downloader modules bypass `get_session()` and perform raw requests:
1. `flareDL/flare_dl.py:98`: `r = requests.post(f"{API_BASE}/v1/h5_open_data", ...)`
2. `flareDL/flare_dl.py:149`: `dl_resp = requests.post(f"{API_BASE}/v1/h5/download_file_url", ...)`
3. `flezenDL/flezen_dl.py:37`: `session = requests.Session()` (instantiated anew in `_get_flezen_session`)
4. `flezenDL/flezen_dl.py:97`: `session = _get_flezen_session() or requests.Session()` (called per `get_flezen_info` call)
5. `diskwalaDL/public_api.py:102`: `resp = requests.post(DISKWALA_PROXY_URL, ...)`
6. `scripts/auto_flezen_cookie.py:57`: `session = requests.Session()`

**Impact**: Repeated TLS handshakes, socket allocation churn, and inability to reuse TCP connections across worker threads.

#### C. Session Lifecycle & Teardown
- `get_session()` creates a singleton `requests.Session`, but there is no `close_session()` cleanup hook in `main.py` lifespan teardown, leaving open socket descriptors during shutdown until process exit.

### 2.3 Proposed Fixes & Architectural Recommendations

1. **Implement Safe, Reentrant DNS Caching**:
   ```python
   _orig_getaddrinfo = socket.getaddrinfo

   def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
       now = time.time()
       cache_key = (host, port, family, type, proto, flags)
       with _dns_cache_lock:
           entry = _dns_cache.get(cache_key)
           if entry and entry[1] > now:
               return entry[0]

       result = _orig_getaddrinfo(host, port, family, type, proto, flags)
       if result:
           with _dns_cache_lock:
               _dns_cache[cache_key] = (result, now + _dns_TTL)
       return result

   def install_dns_cache():
       if socket.getaddrinfo is not _cached_getaddrinfo:
           socket.getaddrinfo = _cached_getaddrinfo
   ```
2. **Standardize All Downloaders onto `get_session()`**:
   - Update `flareDL/flare_dl.py` to use `get_session().post(...)`.
   - Update `flezenDL/flezen_dl.py` to configure a dedicated pooled adapter or clone default headers on `get_session()`.
   - Update `diskwalaDL/public_api.py` to use `get_session().post(...)`.
3. **Register Lifespan Cleanup for Global Session**:
   - Provide `close_session()` in `network.py` and call it in `main.py:lifespan` teardown.

---

## 3. Deep Dive: FastTelethon Parallel Chunk Upload Concurrency (`fast_upload.py`)

### 3.1 Current Implementation in `telegram_logic/fast_upload.py`
`fast_upload.py` implements parallel chunked uploading to Telegram using `SaveBigFilePartRequest` with 512KB chunks:

```python
CHUNK_SIZE = 512 * 1024  # 512KB
MAX_PARALLEL = 4         # Parallel upload streams
```

### 3.2 Critical Vulnerabilities & Defects Identified

#### A. Out-of-Order Progress Reporting Jumps (`fast_upload.py:41`)
- **Observation**: In `_send_partial`:
  ```python
  if progress_callback:
      bytes_sent = min((file_part + 1) * CHUNK_SIZE, file_size)
      progress_callback(bytes_sent, file_size)
  ```
- **Bug**: Because `MAX_PARALLEL` chunks upload concurrently, they complete out of order. For example, part 4 (2.5MB) may complete before part 1 (1.0MB).
  - Callback receives: `2.5MB -> 1.0MB -> 3.0MB -> 1.5MB`.
  - The Telegram status message jumps wildly between percentages (e.g., `45% -> 12% -> 50% -> 20%`).
- **Fix**: Use a thread-safe / atomic completed bytes counter:
  ```python
  uploaded_bytes = 0
  uploaded_lock = asyncio.Lock()
  # on chunk complete:
  async with uploaded_lock:
      uploaded_bytes += len(chunk_data)
      if progress_callback:
          progress_callback(uploaded_bytes, file_size)
  ```

#### B. Unbounded Task Explosion on Large Files (`fast_upload.py:96`)
- **Observation**:
  ```python
  tasks = [asyncio.create_task(_send_with_sem(i)) for i in range(file_total_parts)]
  await asyncio.gather(*tasks)
  ```
- **Bug**: For a 2GB video, `file_total_parts = 4000`. 4,000 coroutine objects and `Task` structures are allocated at once on the event loop. While the semaphore bounds network concurrency, the event loop memory footprint and task scheduling overhead spike unnecessarily.
- **Fix**: Worker pool pattern with `asyncio.Queue` or worker tasks:
  ```python
  queue = asyncio.Queue(maxsize=MAX_PARALLEL * 2)
  # spawn exactly MAX_PARALLEL worker tasks reading from queue
  ```

#### C. Zero Retry Resilience for Chunk Upload Failures (`fast_upload.py:32-39`)
- **Observation**: `_send_partial` executes `await client._sender.send(...)` with no retry loop or exception catching.
- **Bug**: If Telegram DC drops a connection or returns an RPC error on part 3,999 of a 2,000MB upload, the entire `asyncio.gather` raises and aborts the entire transfer.
- **Fix**: Retry chunk uploads up to 4 times with exponential backoff before failing.

#### D. Inconsistent & Broken Engine Call Signatures
Auditing all upload pipelines revealed widespread typing and calling bugs:
1. `telegram_logic/flare.py:216`:
   ```python
   _pre_upload_file(filepath, cancel_event=cancel_event, progress_callback=ul_progress_cb)
   ```
   `bot._pre_upload_file` signature is `(filepath: str, progress_cb=None)`. Calling with `cancel_event` or `progress_callback` raises `TypeError`.
2. `telegram_logic/flezen.py:208 & 213`:
   ```python
   upload_cb = make_upload_progress_cb(status, filename, size_bytes, cancel_event)
   uploaded_file = await _pre_upload_file(
       downloaded_path, progress_callback=upload_cb, cancel_event=cancel_event
   )
   ```
   - Passes `cancel_event` into `loop` parameter of `make_upload_progress_cb`, breaking `asyncio.run_coroutine_threadsafe`.
   - Passes unexpected keyword args to `_pre_upload_file`.
3. `telegram_logic/universal.py:228`:
   - Directly calls `bot.send_file(STORAGE_GROUP_ID, filepath, ...)` without using `_pre_upload_file` or `upload_file_fast`.
4. Direct Upload Fallbacks (when `STORAGE_GROUP_ID` is empty):
   - In `terabox_exp.py`, `diskwala.py`, and `social_dl.py`, if `STORAGE_GROUP_ID` is not configured, `_pre_upload_file` is completely skipped and files are uploaded via standard sequential `send_file`. `_pre_upload_file` should be used regardless of whether a storage warehouse exists.

---

## 4. Deep Dive: Background Storage Cleanup Loops, Disk GC & Concurrency Safety

### 4.1 Current Implementation in `main.py`
`main.py:260-295` runs `_storage_cleanup_loop` every 120 seconds:

```python
for d in target_dirs: # ["storage", "downloads"]
    for f in glob.glob(os.path.join(d, "*")):
        if os.path.isfile(f):
            age = now - os.path.getmtime(f)
            if age > 600: # 10 minutes
                os.remove(f)
```

### 4.2 Critical Vulnerabilities & Defects Identified

#### A. Active Transfer vs. GC Race Condition
- **Observation**: The cleanup loop evaluates `now - os.path.getmtime(f) > 600` blindly against files in `storage/` and `downloads/`.
- **Race Condition**:
  - Download starts at $T_0$. File is created on disk at $T_0$.
  - Large download (e.g. 1.8GB) completes at $T_0 + 6\text{ mins}$. `mtime` reflects creation/modification time.
  - Video upload starts at $T_0 + 6\text{ mins}$. Over a 3–4 MB/s uplink, upload takes 8–10 minutes.
  - At $T_0 + 10\text{ mins}$, `now - mtime` exceeds 600 seconds.
  - `_storage_cleanup_loop` wakes up at $T_0 + 10\text{ mins}$ or $T_0 + 12\text{ mins}$, finds `age > 600`, and unlinks `storage/file.mp4` while `fast_upload` is in the middle of uploading it!
- **Fix**: Centralized active file registry:
  ```python
  # telegram_logic/bot.py
  _active_disk_paths: set[str] = set()
  _active_disk_lock = threading.Lock()
  ```
  `_storage_cleanup_loop` must check `if f in _active_disk_paths: continue` and never delete an active file regardless of its age.

#### B. Incomplete Target Directory & Artifact Coverage
- **Observation**: `_storage_cleanup_loop` only scans `storage/*` and `downloads/*`.
- **Missing Paths**:
  1. `tempfile.gettempdir()` (`/tmp`): `terabox_exp.py:125` writes `terabox_{cache_key}.m3u8` to `/tmp`. If the bot crashes or an unhandled exception occurs, `/tmp/terabox_*.m3u8` files are never cleaned up.
  2. Thumbnail leftovers (`*_thumb.jpg`): If an exception occurs before `_cleanup_files`, thumbnails in `/tmp` or `storage` remain orphaned.
  3. Intermediate `.ts` stream files and `.parts` folders: Aborted HLS streams leave `.ts` and `.parts` directories.
  4. yt-dlp temporary `.part` / `.ytdl` files in `storage/`.

#### C. Multi-Part Download Disk Amplification (200% Peak Usage)
- **Observation**: `teraboxDL/public_api.py:198-260` (`_download_video_multipart`) downloads 4 separate part files (`part_paths = [part_0, part_1, part_2, part_3]`) into `.parts/`, then performs `shutil.copyfileobj(p, out)` to stitch them into `download_path`.
- **Defect**: A 2GB download requires 2GB for parts + 2GB for the output file = 4GB total peak disk usage!
- **Optimization**: Pre-allocate output file (`open(download_path, "wb")`) and have each worker write directly to its byte offset using `open(download_path, "r+b")` with `seek(start)` or `os.pwrite()`. This completely eliminates `.parts/` directories, eliminates the copying pass, and cuts disk I/O by 50%.

#### D. GC Sweeps & Resident Memory Tracking
- `_memory_monitor_loop` in `main.py` calls `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`. On Linux, `ru_maxrss` is the monotonic peak memory (never decreases even after full garbage collection).
- To track actual current memory, inspect `/proc/self/status` (`VmRSS`) or `/proc/self/statm`.
- Proactive `gc.collect(1)` sweeps should be triggered in `finally` blocks after large uploads complete to immediately release buffer memory back to the allocator.

---

## 5. Summary of Recommended Code Modifications

| Component | Target File | Issue | Recommended Fix |
|---|---|---|---|
| **Network** | `network.py` | Inactive DNS cache with recursion bug | Install safe reentrant DNS cache wrapper with IPv6 support. |
| **Network** | `flareDL/flare_dl.py` | Raw `requests.post` calls | Replace with `get_session().post(...)`. |
| **Network** | `flezenDL/flezen_dl.py` | Unpooled `requests.Session()` creation | Use pooled session adapter with cookies. |
| **Network** | `diskwalaDL/public_api.py` | Raw `requests.post` to proxy | Replace with `get_session().post(...)`. |
| **Upload** | `telegram_logic/fast_upload.py` | Out-of-order progress jumping | Atomic progress accumulator with lock. |
| **Upload** | `telegram_logic/fast_upload.py` | 4,000 task explosion on 2GB files | Bounded worker pool (`asyncio.Queue` / worker coroutines). |
| **Upload** | `telegram_logic/fast_upload.py` | Zero chunk upload retry | Retry failed chunks 3x with backoff in `_send_partial`. |
| **Upload** | `telegram_logic/flare.py` | `TypeError` in `_pre_upload_file` | Align signature with `(filepath, progress_cb)`. |
| **Upload** | `telegram_logic/flezen.py` | Invalid args in progress cb & upload | Fix `make_upload_progress_cb` args and `_upload_to_storage`. |
| **Upload** | `telegram_logic/universal.py` | Bypasses `fast_upload` | Route large files through `_pre_upload_file`. |
| **Storage GC** | `main.py` | GC race condition with active files | Track `_active_disk_paths` set; skip active files during cleanup. |
| **Storage GC** | `main.py` | Incomplete artifact coverage | Add `/tmp/terabox_*.m3u8`, thumbnails, and orphaned `.parts` to GC. |
| **Download GC** | `teraboxDL/public_api.py` | 2x disk usage in multi-part download | Use positional writes (`r+b`/`os.pwrite`) to avoid `.parts/` copy pass. |

---

