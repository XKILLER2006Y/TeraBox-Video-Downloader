"""
TeraBox Pipeline Diagnostic Test
=================================
Tests each step of the metadata-fetch → download pipeline to pinpoint failures.

Usage:
    python test.py                                          # test with a default sample URL
    python test.py "https://1024terabox.com/s/1abc123def"   # test with a specific URL
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Colour helpers (works on Windows 10+ terminals) ──────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN}[PASS]{RESET}  {msg}")
def fail(msg):  print(f"  {RED}[FAIL]{RESET}  {msg}")
def warn(msg):  print(f"  {YELLOW}[WARN]{RESET}  {msg}")
def info(msg):  print(f"  {CYAN}[INFO]{RESET}  {msg}")
def header(msg): print(f"\n{BOLD}{'='*60}\n  {msg}\n{'='*60}{RESET}")


passed = 0
failed = 0

def check(condition, pass_msg, fail_msg):
    global passed, failed
    if condition:
        ok(pass_msg)
        passed += 1
    else:
        fail(fail_msg)
        failed += 1
    return condition


# ── Default test URL (uses the surl from your error logs) ────────────────────
DEFAULT_TEST_URL = "https://1024terabox.com/s/1UZ3Sv9ff1Ck0p-B_nimrg"

def main():
    test_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEST_URL
    print(f"\n{BOLD}TeraBox Pipeline Diagnostic Test{RESET}")
    print(f"  Test URL: {test_url}\n")

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 1: Environment Variables")
    # ═══════════════════════════════════════════════════════════════════════

    PROXY_URL = os.getenv("PROXY_URL")
    THIRD_PARTY_TERABOXDL_URL = os.getenv("THIRD_PARTY_TERABOXDL_URL")

    check(PROXY_URL, f"PROXY_URL = {PROXY_URL}", "PROXY_URL is not set in .env")
    check(THIRD_PARTY_TERABOXDL_URL, f"THIRD_PARTY_TERABOXDL_URL = {THIRD_PARTY_TERABOXDL_URL}",
          "THIRD_PARTY_TERABOXDL_URL is not set in .env")

    if not PROXY_URL or not THIRD_PARTY_TERABOXDL_URL:
        fail("Cannot continue without environment variables. Fix .env and retry.")
        return

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 2: Proxy Server Connectivity")
    # ═══════════════════════════════════════════════════════════════════════

    try:
        r = requests.get(PROXY_URL, timeout=10)
        check(True, f"Proxy reachable -- HTTP {r.status_code}", f"Proxy returned HTTP {r.status_code}")
    except Exception as e:
        check(False, "", f"Proxy unreachable: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 3: Metadata Fetch (POST to proxy)")
    # ═══════════════════════════════════════════════════════════════════════

    payload = {
        "cmd": "request.post2",
        "base_url": THIRD_PARTY_TERABOXDL_URL,
        "post_endpoint": "api/proxy",
        "post_json_body": json.dumps({"url": test_url}),
    }

    info(f"POST {PROXY_URL}")
    info(f"Payload: {json.dumps(payload, indent=2)}")

    response = None
    response_dict = None
    try:
        t0 = time.time()
        response = requests.post(PROXY_URL, json=payload, timeout=600)
        elapsed = time.time() - t0
        check(response.status_code == 200,
              f"HTTP {response.status_code} in {elapsed:.1f}s",
              f"HTTP {response.status_code} — expected 200. Body: {response.text[:500]}")
    except Exception as e:
        check(False, "", f"POST request failed: {e}")
        return

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 4: Parse Proxy Response")
    # ═══════════════════════════════════════════════════════════════════════

    try:
        response_dict = response.json()
        check(True, "Response is valid JSON", "Response is NOT valid JSON")
    except Exception as e:
        check(False, "", f"Response is NOT valid JSON: {e}")
        info(f"Raw body (first 500 chars): {response.text[:500]}")
        return

    # Print full response structure for debugging
    info("Full response keys: " + str(list(response_dict.keys())))
    print(f"\n{CYAN}  Full response (pretty-printed):{RESET}")
    print(json.dumps(response_dict, indent=2, default=str)[:3000])
    print()

    # Save response to JSON file
    # Extract surl from the test URL for the filename
    surl = test_url.rstrip("/").split("/")[-1]
    output_file = f"response_{surl}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(response_dict, f, indent=2, ensure_ascii=False)
    info(f"Response saved to: {output_file}")

    # Check for time_taken field
    time_taken = response_dict.get("time_taken")
    if time_taken:
        info(f"time_taken = {time_taken}")

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 5: Extract target_url_response")
    # ═══════════════════════════════════════════════════════════════════════

    target_url_response = response_dict.get("target_url_response")
    check(target_url_response is not None,
          f"'target_url_response' found (type: {type(target_url_response).__name__})",
          "'target_url_response' is MISSING from proxy response")

    if target_url_response is None:
        warn("The proxy may have changed its response format.")
        warn("Available keys: " + str(list(response_dict.keys())))
        return

    info("target_url_response keys: " + str(list(target_url_response.keys()) if isinstance(target_url_response, dict) else "NOT A DICT"))

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 6: Extract 'body' from target_url_response")
    # ═══════════════════════════════════════════════════════════════════════
    #
    # THIS IS WHERE YOUR FIRST ERROR HAPPENS:
    #   'Response' object has no attribute 'body'
    #
    # The code in terabox_dl.py does: target_url_response.get("body")
    # If the proxy changed its format, "body" may no longer exist.

    body = None
    if isinstance(target_url_response, dict):
        body = target_url_response.get("body")

        if body is not None:
            check(True, "'body' key found in target_url_response", "'body' key not found")
        else:
            check(False, "", "'body' key is MISSING from target_url_response")
            warn("This is the cause of: 'Response' object has no attribute 'body'")
            warn("Available keys in target_url_response: " + str(list(target_url_response.keys())))

            # Try to find the data elsewhere — maybe it's directly in target_url_response
            if "list" in target_url_response:
                warn("Found 'list' directly in target_url_response (not nested under 'body')!")
                warn("FIX: Update terabox_dl.py to use target_url_response directly instead of target_url_response['body']")
                body = target_url_response  # use it directly for further testing
            elif "body" not in target_url_response:
                # Check if body is a string that needs parsing
                for key in target_url_response:
                    val = target_url_response[key]
                    if isinstance(val, str):
                        try:
                            parsed = json.loads(val)
                            if isinstance(parsed, dict) and "list" in parsed:
                                warn(f"Found 'list' inside target_url_response['{key}'] (as JSON string)")
                                body = parsed
                                break
                        except (json.JSONDecodeError, TypeError):
                            pass
    else:
        check(False, "", f"target_url_response is {type(target_url_response).__name__}, expected dict")

    if body is None:
        fail("Cannot extract video metadata — body is None. Cannot continue.")
        return

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 7: Validate Video Metadata (the 'body' / data)")
    # ═══════════════════════════════════════════════════════════════════════

    if isinstance(body, str):
        try:
            body = json.loads(body)
            info("body was a JSON string — parsed successfully")
        except json.JSONDecodeError:
            check(False, "", f"body is a string but not valid JSON: {body[:200]}")
            return

    check(isinstance(body, dict), f"body is a dict with keys: {list(body.keys()) if isinstance(body, dict) else 'N/A'}",
          f"body is {type(body).__name__}, expected dict")

    if not isinstance(body, dict):
        return

    # Check for error in body
    errno = body.get("errno", 0)
    errmsg = body.get("errmsg", "")
    if errno != 0:
        check(False, "", f"TeraBox API returned errno={errno}: {errmsg}")
        warn("Common errno values: 105 = File removed, 2 = Invalid link, -6 = Rate limited")
        warn("This specific TeraBox URL is invalid/expired. Try a different URL.")
        # Print summary and exit
        header("SUMMARY")
        print(f"\n  {GREEN}{passed} passed{RESET}  |  {RED}{failed} failed{RESET}\n")
        print(f"  {RED}{BOLD}The TeraBox link is dead/invalid (errno={errno}).{RESET}")
        print(f"  {YELLOW}Try testing with a valid/active TeraBox share link.{RESET}\n")
        return

    if body.get("error"):
        check(False, "", f"body contains error: {body.get('message', body.get('error'))}")
        return

    file_list = body.get("list", [])
    check(len(file_list) > 0, f"Found {len(file_list)} file(s) in list",
          "File list is empty or missing -- the TeraBox URL may be invalid or expired")

    if not file_list:
        return

    file_info = file_list[0]
    info(f"Filename: {file_info.get('server_filename', 'N/A')}")
    info(f"Size: {file_info.get('size', 'N/A')} bytes ({file_info.get('formatted_size', 'N/A')})")
    info(f"Duration: {file_info.get('duration', 'N/A')}s")

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 8: Extract Download URLs")
    # ═══════════════════════════════════════════════════════════════════════
    #
    # THIS IS WHERE YOUR SECOND ERROR HAPPENS:
    #   Invalid URL '': No scheme supplied
    #
    # If direct_link or stream_download_url is empty/missing, the download fails.

    direct_link = file_info.get("direct_link", "")
    stream_download_url = file_info.get("stream_download_url", "")

    check(bool(direct_link), f"direct_link (HD) present ({len(direct_link)} chars)",
          "direct_link is EMPTY — HD downloads will fail with 'Invalid URL'")

    check(bool(stream_download_url), f"stream_download_url (SD) present ({len(stream_download_url)} chars)",
          "stream_download_url is EMPTY — SD downloads will fail with 'Invalid URL'")

    if direct_link:
        info(f"direct_link starts with: {direct_link[:80]}...")
    if stream_download_url:
        info(f"stream_download_url starts with: {stream_download_url[:80]}...")

    # Pick the URL that would be used for non-HD mode (stream_download_url)
    test_download_url = stream_download_url or direct_link

    if not test_download_url:
        fail("Both download URLs are empty! The API returned no usable download link.")
        warn("This means the proxy/third-party API did not generate download tokens.")
        return

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 9: Test Download URL Reachability (HEAD request)")
    # ═══════════════════════════════════════════════════════════════════════

    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": test_download_url.split("/")[0] + "//" + test_download_url.split("/")[2] + "/",
    }

    try:
        r = requests.head(test_download_url, headers=BROWSER_HEADERS, allow_redirects=True, timeout=15)
        status_code = r.status_code
        content_length = r.headers.get("Content-Length", "unknown")
        accept_ranges = r.headers.get("Accept-Ranges", "none")

        check(status_code in (200, 206),
              f"Download URL reachable — HTTP {status_code}",
              f"Download URL returned HTTP {status_code}")

        info(f"Content-Length: {content_length}")
        info(f"Accept-Ranges: {accept_ranges}")
        info(f"Content-Type: {r.headers.get('Content-Type', 'unknown')}")

        if accept_ranges.lower() == "bytes":
            ok("Server supports Range requests → multi-part download will work")
        else:
            warn("Server does NOT support Range → will fall back to single-stream")

    except Exception as e:
        check(False, "", f"HEAD request to download URL failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    header("Step 10: Test Download (first 64KB)")
    # ═══════════════════════════════════════════════════════════════════════

    try:
        r = requests.get(test_download_url, headers=BROWSER_HEADERS, stream=True, timeout=30)
        r.raise_for_status()

        chunk = next(r.iter_content(65536), None)
        r.close()

        if chunk and len(chunk) > 0:
            check(True, f"Successfully downloaded first {len(chunk)} bytes", "Download check failed")
        else:
            check(False, "", "Got empty response from download URL")

    except Exception as e:
        check(False, "", f"Test download failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    header("SUMMARY")
    # ═══════════════════════════════════════════════════════════════════════

    print(f"\n  {GREEN}{passed} passed{RESET}  |  {RED}{failed} failed{RESET}\n")

    if failed == 0:
        print(f"  {GREEN}{BOLD}All checks passed! The pipeline is working correctly.{RESET}\n")
    else:
        print(f"  {RED}{BOLD}Some checks failed. See details above.{RESET}")
        print(f"\n  {YELLOW}Common fixes:{RESET}")
        print(f"    1. If 'body' is missing → the proxy response format changed.")
        print(f"       Update terabox_dl.py line 45-48 to handle the new structure.")
        print(f"    2. If download URLs are empty → the third-party API failed to")
        print(f"       generate download tokens. Try a different TeraBox URL.")
        print(f"    3. If proxy is unreachable → check PROXY_URL in .env\n")


if __name__ == "__main__":
    main()
