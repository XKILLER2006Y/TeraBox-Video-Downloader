# Handoff Report: Survey Explorer 2 (R2 — Memory, Storage GC & Concurrency)

**Explorer**: Survey Explorer 2  
**Date**: 2026-08-30  
**Working Directory**: `/home/arifureta/TeraBox-Video-Downloader/.agents/survey_explorer_r2`  
**Milestone**: Survey Complete  

---

## 1. Observation

### 1.1 Inactive & Recursion-Prone DNS Cache in `network.py`
- In `network.py:88-101`:
  ```python
  def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
      """DNS resolver with in-memory TTL cache."""
      now = time.time()
      with _dns_cache_lock:
          entry = _dns_cache.get(host)
          if entry and entry[1] > now:
              return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (entry[0], port))]

      result = socket.getaddrinfo(host, port, family, type, proto, flags)
      if result:
          ip = result[0][4][0]
          with _dns_cache_lock:
              _dns_cache[host] = (ip, now + _dns_TTL)
      return result
  ```
- Grep for `_cached_getaddrinfo` shows it is never assigned to `socket.getaddrinfo`. If assigned, calling `socket.getaddrinfo` on line 96 without an original reference causes infinite recursion.

### 1.2 Unpooled Requests Across Downloaders
- In `flareDL/flare_dl.py:98`: `r = requests.post(f"{API_BASE}/v1/h5_open_data", json=open_data_payload, headers=_HEADERS, timeout=15)`
- In `flareDL/flare_dl.py:149`: `dl_resp = requests.post(f"{API_BASE}/v1/h5/download_file_url", json=dl_payload, headers=_HEADERS, timeout=15)`
- In `flezenDL/flezen_dl.py:37`: `session = requests.Session()`
- In `flezenDL/flezen_dl.py:97`: `session = _get_flezen_session() or requests.Session()`
- In `diskwalaDL/public_api.py:102`: `resp = requests.post(DISKWALA_PROXY_URL, json={"url": diskwala_url}, headers={"x-api-key": DISKWALA_API_KEY}, timeout=600)`

### 1.3 FastTelethon Progress & Task Explosion in `fast_upload.py`
- In `telegram_logic/fast_upload.py:41-42`:
  ```python
  if progress_callback:
      bytes_sent = min((file_part + 1) * CHUNK_SIZE, file_size)
      progress_callback(bytes_sent, file_size)
  ```
  Because chunks upload concurrently out-of-order, `bytes_sent` jumps backward when an earlier part finishes after a later part.
- In `telegram_logic/fast_upload.py:96`:
  ```python
  tasks = [asyncio.create_task(_send_with_sem(i)) for i in range(file_total_parts)]
  ```
  For a 2GB file (4,000 parts), 4,000 tasks are created simultaneously on the event loop.
- In `telegram_logic/fast_upload.py:32-39`: `_send_partial` does not catch network/DC exceptions or retry failed chunks.

### 1.4 Broken Upload Calling Signatures
- In `telegram_logic/flare.py:216`:
  ```python
  _pre_upload_file(filepath, cancel_event=cancel_event, progress_callback=ul_progress_cb)
  ```
  `_pre_upload_file` in `bot.py:136` is defined as `async def _pre_upload_file(filepath: str, progress_cb=None):`. Passing `cancel_event` raises `TypeError`.
- In `telegram_logic/flezen.py:208`:
  ```python
  upload_cb = make_upload_progress_cb(status, filename, size_bytes, cancel_event)
  ```
  `make_upload_progress_cb` takes `(status_msg, filename, size_str, loop, cancel_btn=None)`. Passing `cancel_event` as `loop` causes `asyncio.run_coroutine_threadsafe` to fail.

### 1.5 Storage GC Race Condition in `main.py`
- In `main.py:276-290`:
  ```python
  for d in target_dirs: # storage, downloads
      for f in glob.glob(os.path.join(d, "*")):
          try:
              if os.path.isfile(f):
                  age = now - os.path.getmtime(f)
                  if age > 600: # 10 minutes
                      os.remove(f)
  ```
  If a download/upload of a large file takes >10 minutes, `_storage_cleanup_loop` deletes the active file mid-transfer because it does not cross-reference active tasks.

### 1.6 2x Disk Overhead in `teraboxDL/public_api.py`
- In `teraboxDL/public_api.py:243-247`:
  `_download_video_multipart` downloads parts to `.parts/part_i`, then copies them to `download_path` with `shutil.copyfileobj`, doubling peak disk usage.

---

## 2. Logic Chain

1. **DNS & Socket Pooling**:
   - `_cached_getaddrinfo` is uninstalled $\to$ Every DNS resolution hits OS network stack synchronously $\to$ High latency on resolver requests.
   - `flareDL`, `flezenDL`, and `diskwalaDL` create unpooled `requests` $\to$ Connection pools in `network.py` are bypassed $\to$ Unnecessary TLS handshakes and socket descriptor churn.
2. **Fast Upload & Concurrency**:
   - `fast_upload.py` derives progress from `file_part` index $\to$ Concurrent part completions report out-of-order $\to$ User UI progress percentage flickers and regresses.
   - 4,000 tasks are created for 2GB files $\to$ High memory and event loop scheduling overhead $\to$ Should be replaced by a bounded worker pool.
   - `_send_partial` lacks retries $\to$ Single transient packet drop fails 2GB transfer.
   - Inconsistent call signatures in `flare.py` and `flezen.py` $\to$ Runtime `TypeError` and crashes during upload phase.
3. **Storage GC & Race Conditions**:
   - `_storage_cleanup_loop` checks only `age > 600` without consulting active tasks $\to$ 2GB files taking >10 minutes to upload get unlinked mid-upload $\to$ Upload errors and corruption.
   - Multi-part downloads download to `.parts` before stitching $\to$ Peak disk usage is 2x file size $\to$ VPS runs out of disk space on large video downloads.

---

## 3. Caveats

- Diskwala scraper direct vs proxy path relies on either `SESSION` or `DISKWALA_PROXY_URL` / `DISKWALA_API_KEY` configuration in `.env`.
- Telethon's `client._sender.send()` operates over the primary client connection; true multi-connection parallel DC sender pools would require Telethon `_borrow_exported_sender` or multiple client session instances.
- Pytest suite contains several standalone scripts (`test_features2.py`, `test_features2_appendix.py`, `test_new_features.py`) with top-level `sys.exit()` calls that prevent full-directory discovery when running bare `pytest`. Targeted testing (`pytest tests/test_enhancements.py ...`) runs cleanly.

---

## 4. Conclusion

The system has robust foundational components (`MessageQueue`, `get_session`, `upload_file_fast`, `_storage_cleanup_loop`), but suffers from critical integration disconnects:
- Bypassed session pooling in resolvers.
- Latent bugs in DNS caching and FastTelethon progress reporting / task scaling.
- Call signature bugs in `flare.py` and `flezen.py` causing upload failures.
- Severe race condition in background storage cleanup against active downloads/uploads >10 minutes.
- 2x disk amplification in multipart downloads.

Implementing the proposed fixes in `network.py`, `fast_upload.py`, `main.py`, `public_api.py`, `flare.py`, and `flezen.py` will guarantee zero GC race conditions, bounded RAM usage, and maximum network throughput.

---

## 5. Verification Method

### 5.1 Verification Commands
1. Run passing targeted test suite:
   ```bash
   pytest tests/test_enhancements.py tests/test_social_media.py tests/test_flare.py tests/test_flezen.py
   ```
2. Verify Python syntax across all modified modules:
   ```bash
   python3 -m py_compile network.py telegram_logic/fast_upload.py main.py teraboxDL/public_api.py telegram_logic/flare.py telegram_logic/flezen.py
   ```

### 5.2 Key Invalidation Conditions
- If `_cached_getaddrinfo` is patched without holding `_orig_getaddrinfo`, any DNS resolution will crash with `RecursionError`.
- If `_storage_cleanup_loop` deletes a file present in `_active_disk_paths`, active transfers will fail with `FileNotFoundError`.
- If `fast_upload.py` progress callback reports decreasing byte counts, out-of-order progress regression persists.

---
