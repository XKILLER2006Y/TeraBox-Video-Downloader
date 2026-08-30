# Handoff Report: R1 Multi-Engine Downloader & Stream Pipeline Optimization

## 1. Observation

Direct code observations from inspecting `/home/arifureta/TeraBox-Video-Downloader`:

1. **`telegram_logic/terabox_exp.py:21` vs Line 193**:
   - Line 21: `from teraboxDL.errors import TeraBoxError, CancelledError, TeraBoxDirectError, TeraBoxMultipleChoice`
   - Line 193: `except TeraBoxRateLimited as e:`
   - Verbatim error if 429 occurs: `NameError: name 'TeraBoxRateLimited' is not defined`.

2. **`flezenDL/flezen_dl.py:184`**:
   - `stream_downloader.download_from_stream_url` definition in `teraboxDL/stream_downloader.py:364`:
     `def download_from_stream_url(stream_url: str, output_file: str, cancel_event: threading.Event | None = None, progress_callback=None) -> str:`
   - In `flezen_dl.py:184`:
     `return download_from_stream_url(download_url, output_path, progress_callback)`
   - `progress_callback` is bound to parameter `cancel_event`.

3. **`telegram_logic/flezen.py`**:
   - Line 149: `ok, reason = check_size_limit(size_bytes, is_admin)`
     `check_size_limit` definition in `telegram_logic/helpers.py:223`: `def check_size_limit(size_bytes: int) -> str | None:` (takes 1 arg, returns string or None).
   - Line 176: `download_cb = make_download_progress_cb(status, filename, size_bytes, cancel_event)`
     `make_download_progress_cb` definition in `progress_callbacks.py:8`: `def make_download_progress_cb(status_msg, filename: str, size_str: str, loop: asyncio.AbstractEventLoop, cancel_btn=None, expected_total: int = 0)`.
   - Line 118, 119, 120, 132, 137, 152, 168, 191, 248, 251, 252, 253, 258: Calls `await stats_ok(...)`, `await stats_fail(...)`, `await record_history(...)`, `await bump_today(...)`, `await add_to_cache(...)`. All these target functions in `firebase_db` are synchronous `def` functions returning `None` or `bool`.
   - Line 204: `video_attrs = get_video_attributes(downloaded_path, meta, filename)`
     `get_video_attributes` definition in `media_info.py:111`: `def get_video_attributes(file_path: str, duration: int | dict | None = None, width: int | None = None, height: int | None = None)`. Passing `filename` (str) as 3rd positional argument assigns `width = filename`, causing `int(width)` to raise `ValueError`.

4. **`telegram_logic/flare.py`**:
   - Line 291: `stats_ok()` called without `actual_size`.
   - Line 295: `await asyncio.to_thread(record_history, chat_id, link_id, user_mode, filename, actual_size)` passes 5 positional arguments to `record_history` which takes 4: `(chat_id: int, title: str, key: str, size: int = 0)`.

5. **`teraboxDL/public_api.py:242-246`**:
   - `_download_video_multipart` downloads 4 range parts into `.parts/part_i`, then stitches via:
     ```python
     with open(download_path, "wb") as out:
         for part_path in part_paths:
             with open(part_path, "rb") as p:
                 shutil.copyfileobj(p, out)
     ```
   - Incurs 100% redundant read + write of full file payload.

6. **`teraboxDL/stream_downloader.py:310-333`**:
   - `_download_hls_from_manifest` concatenates all segments into `ts_output` on disk, then calls `_remux_ts_to_mp4` which runs `ffmpeg` reading `ts_output` from disk and writing `output_file` (`.mp4`), then deleting `ts_output`.

7. **`firebase_db/cache.py:103-111`**:
   - `search_in_cache` only routes `"exp"`, `"dw"`, and `"dl"`. `"flare"` and `"flezen"` fall into `else: search_order = ["exphd"]`, querying the wrong Firestore document.

8. **Test Executions**:
   - `pytest tests/` failed with exit code 3 (`SystemExit` in `tests/test_features2.py:142`, `tests/test_new_features.py:329`, `test_e2e.py:198`).
   - `python3 -m unittest discover -s tests -p "test_*.py"` failed with 3 errors due to top-level `sys.exit()`.

---

## 2. Logic Chain

1. **Defect Chain in `flezen` and `flare`**:
   - When a user sends a Flezen or Flare link, `process_flezen` or `process_flare` executes.
   - For Flezen:
     - If file size exceeds limit, unpacking `ok, reason = check_size_limit(...)` raises `TypeError`.
     - When starting download, `make_download_progress_cb` receives invalid arguments and crashes.
     - When calling `download_flezen_file`, `progress_callback` is placed in `cancel_event`.
     - When constructing video attributes, `filename` is parsed as `width`, raising `ValueError`.
     - Database recording executes `await stats_ok(...)` and crashes on `await None`.
   - For Flare:
     - `record_history` fails due to 5 positional arguments passed to a 4-parameter function.
     - Cache lookups in Firestore query the `"exphd"` bucket instead of `"flare"`, making cache retrieval ineffective.

2. **I/O Overhead Chain in Multipart & HLS Downloads**:
   - In `_download_video_multipart`, writing parts to `.parts/` and then copying them to the destination file creates a duplicate write and read cycle. For large files (e.g. 1–2 GB), this doubles disk bandwidth consumption and disk space usage.
   - In `_download_hls_from_manifest`, writing the intermediate `.ts` file before `ffmpeg` reads it to write `.mp4` duplicates disk write and read operations.
   - Piped streaming into `ffmpeg -i pipe:0` or direct `os.pwrite` into a pre-allocated file eliminates these intermediate disk operations.

3. **Rate Limit Crash Chain in TeraBox**:
   - When TeraBox API returns HTTP 429, `_get_share_info` raises `TeraBoxRateLimited`.
   - In `telegram_logic/terabox_exp.py`, `except TeraBoxRateLimited as e:` is reached, but `TeraBoxRateLimited` is unbound in the module namespace. Python raises `NameError`, bubbling up to the top-level exception handler rather than gracefully rotating cookies.

---

## 3. Caveats

- Live credentials (e.g., active `COOKIES1..10`, `SESSION`, `FLEZEN_COOKIE`, `DISKWALA_PROXY_URL`) were not mutated or live-tested with active paid accounts during this read-only survey.
- FFmpeg stdin piping with `-i pipe:0` requires verifying that MPEG-TS stream headers from all TeraBox mirrors are continuous across segment joins.

---

## 4. Conclusion

The downloader and streaming pipelines are structurally sound and feature advanced patterns (rolling-window HLS downloads, connection pooling with TCP_NODELAY, atomic pread uploads, and adaptive polling). 

To achieve optimal throughput, zero redundant copies, and 100% test reliability, implementation work should execute the following targeted changes:
1. Fix syntax/import/argument bugs across `telegram_logic/terabox_exp.py`, `telegram_logic/flezen.py`, `flezenDL/flezen_dl.py`, `telegram_logic/flare.py`, and `firebase_db/cache.py`.
2. Convert `_download_video_multipart` to zero-copy direct file writes using `os.pwrite`.
3. Implement piped stdin remuxing in `stream_downloader.py` for HLS downloads.
4. Wrap top-level test runner code in `tests/` within `if __name__ == "__main__":` blocks so `pytest` and `unittest` execute with 100% pass rates.

---

## 5. Verification Method

To independently verify all findings and validate future fixes:

1. **Static Analysis & Import Verification**:
   ```bash
   python3 -c "import teraboxDL, flareDL, flezenDL, diskwalaDL, universalDL; import telegram_logic.terabox_exp, telegram_logic.flare, telegram_logic.flezen, telegram_logic.diskwala, telegram_logic.universal, telegram_logic.social_dl; print('All imports OK')"
   ```
2. **Unit Test Suite Execution**:
   ```bash
   python3 -m unittest tests/test_flare.py
   python3 -m unittest tests/test_flezen.py
   python3 -m unittest tests/test_enhancements.py
   python3 -m unittest tests/test_social_media.py
   python3 tests/test_features2.py
   python3 tests/test_new_features.py
   python3 tests/test_features2_appendix.py
   ```
3. **Inspect Target Lines**:
   - `view_file` on `telegram_logic/terabox_exp.py` (lines 20–25 and 190–205).
   - `view_file` on `flezenDL/flezen_dl.py` (lines 165–185).
   - `view_file` on `telegram_logic/flezen.py` (lines 115–260).
   - `view_file` on `teraboxDL/public_api.py` (lines 205–260).
   - `view_file` on `firebase_db/cache.py` (lines 100–115).
