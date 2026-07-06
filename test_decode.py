"""
Test script for _decode_env_json — simulates every mangled env-var format
that Docker / Coolify / docker-compose can produce.

Run:  python test_decode.py
"""

import os
import re
import json
import base64
import logging
import textwrap

# ─── Set up logging so we can see which strategy fires ───────────────────────
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ─── Copy of _decode_env_json from firebase_db/db.py ─────────────────────────
# (self-contained so we don't need firebase_admin installed to test)

def _decode_env_json(raw: str) -> dict:
    """Try progressively more aggressive strategies to parse *raw* into a dict."""

    # ── 0. Strip BOM / invisible unicode whitespace
    cleaned = raw.strip().lstrip("\ufeff").strip()

    # ── 1. Peel off wrapping quote layers (up to 3 deep)
    for _ in range(3):
        if cleaned.startswith("\\'") and cleaned.endswith("\\'"):
            cleaned = cleaned[2:-2]
        elif cleaned.startswith('\\"') and cleaned.endswith('\\"'):
            cleaned = cleaned[2:-2]
        elif (cleaned.startswith("'") and cleaned.endswith("'")) or \
             (cleaned.startswith('"') and cleaned.endswith('"')):
            cleaned = cleaned[1:-1]
        else:
            break

    # ── 1b. Un-escape remaining backslash-quoted characters
    if '\\"' in cleaned or "\\'" in cleaned:
        cleaned = cleaned.replace('\\"', '"').replace("\\'", "'")

    # ── 2. Try plain json.loads first (fast path)
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            log.debug("Parsed on first attempt (plain JSON).")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 3. Un-double-escape
    try:
        unescaped = cleaned.replace('\\"', '"')
        result = json.loads(unescaped)
        if isinstance(result, dict):
            log.debug("Parsed after un-double-escaping.")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 4. Fix literal \\n
    try:
        fixed_newlines = cleaned.replace("\\\\n", "\n").replace("\\n", "\n")
        result = json.loads(fixed_newlines)
        if isinstance(result, dict):
            log.debug("Parsed after fixing escaped newlines.")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 5. Combined: un-double-escape + fix newlines
    try:
        combined = cleaned.replace('\\"', '"').replace("\\\\n", "\n").replace("\\n", "\n")
        result = json.loads(combined)
        if isinstance(result, dict):
            log.debug("Parsed after combined unescape + newline fix.")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 6. Try base64 decoding
    try:
        decoded_bytes = base64.b64decode(cleaned, validate=True)
        result = json.loads(decoded_bytes.decode("utf-8"))
        if isinstance(result, dict):
            log.debug("Parsed from base64-encoded value.")
            return result
    except Exception:
        pass

    # ── 7. Python-style dict with single quotes
    try:
        import ast
        result = ast.literal_eval(cleaned)
        if isinstance(result, dict):
            log.debug("Parsed via ast.literal_eval (Python dict).")
            return result
    except Exception:
        pass

    # ── 8. Last resort: regex-extract the first JSON object
    try:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
            if isinstance(result, dict):
                log.debug("Parsed via regex JSON extraction.")
                return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── Nothing worked
    preview = cleaned[:120] + ("…" if len(cleaned) > 120 else "")
    raise ValueError(
        f"Could not decode into a JSON dict.\n"
        f"  Length : {len(raw)} chars\n"
        f"  Starts: {repr(raw[:30])}\n"
        f"  Ends  : {repr(raw[-30:])}\n"
        f"  Preview (cleaned): {preview}"
    )


# ─── Fake service-account JSON (with a PEM-like private_key) ─────────────────

SAMPLE_DICT = {
    "type": "service_account",
    "project_id": "telegram-bot-db-aebfb",
    "private_key_id": "deb9d0501f2b30a0927c7f1",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGcY5unA\nmore+key+data+here==\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "firebase-adminsdk@telegram-bot-db-aebfb.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk",
    "universe_domain": "googleapis.com"
}

CLEAN_JSON = json.dumps(SAMPLE_DICT)


# ─── Build test cases ────────────────────────────────────────────────────────

def build_test_cases():
    """Return a list of (name, mangled_string) tuples."""
    cases = []

    # 1. Clean JSON (happy path)
    cases.append(("Clean JSON", CLEAN_JSON))

    # 2. Single-quoted wrapper:  '{...}'
    cases.append(("Single-quoted wrapper", f"'{CLEAN_JSON}'"))

    # 3. Double-quoted wrapper:  "{...}"
    cases.append(("Double-quoted wrapper", f'"{CLEAN_JSON}"'))

    # 4. ★ COOLIFY EXACT FORMAT ★  — \'...\'  with  \"  inside
    #    This is the EXACT format from the user's error log
    escaped_internals = CLEAN_JSON.replace('"', '\\"')
    coolify_format = f"\\'{escaped_internals}\\'"
    cases.append(("Coolify \\' wrapper + \\\" internals (EXACT ERROR FORMAT)", coolify_format))

    # 5. Double-escaped quotes only (no wrapper):  {\"key\": \"val\"}
    cases.append(("Double-escaped quotes (no wrapper)", escaped_internals))

    # 6. Double-quoted + double-escaped:  "{\"key\": \"val\"}"
    cases.append(("Double-quoted + escaped internals", f'"{escaped_internals}"'))

    # 7. Escaped newlines in private_key:  \\n instead of real \n
    escaped_newlines = CLEAN_JSON.replace("\n", "\\n")
    cases.append(("Escaped newlines (\\\\n)", escaped_newlines))

    # 8. Combined: double-escaped quotes + escaped newlines
    combined_mangled = CLEAN_JSON.replace('"', '\\"').replace("\n", "\\n")
    cases.append(("Escaped quotes + escaped newlines", combined_mangled))

    # 9. Base64 encoded
    b64 = base64.b64encode(CLEAN_JSON.encode("utf-8")).decode("ascii")
    cases.append(("Base64 encoded", b64))

    # 10. Python-style single-quoted dict
    py_dict = str(SAMPLE_DICT)
    cases.append(("Python dict (single quotes)", py_dict))

    # 11. BOM prefix
    bom_json = "\ufeff" + CLEAN_JSON
    cases.append(("UTF-8 BOM prefix", bom_json))

    # 12. Extra whitespace + wrapping quotes
    cases.append(("Whitespace + quotes", f"  ' {CLEAN_JSON} '  "))

    # 13. Triple-nested quotes:  '  "  '...'  "  '
    cases.append(("Triple-nested quotes", f"""'"{CLEAN_JSON}"'"""))

    # 14. Garbage prefix/suffix around JSON
    cases.append(("Garbage prefix/suffix", f"EXPORT={CLEAN_JSON};"))

    return cases


# ─── Run all tests ───────────────────────────────────────────────────────────

def main():
    cases = build_test_cases()
    passed = 0
    failed = 0
    width = 60

    print("=" * width)
    print("  _decode_env_json  —  Robustness Test Suite")
    print("=" * width)
    print()

    for i, (name, mangled) in enumerate(cases, 1):
        print(f"Test {i:2d}: {name}")
        print(f"         Input preview: {repr(mangled[:70])}{'…' if len(mangled) > 70 else ''}")
        try:
            result = _decode_env_json(mangled)

            # Verify the parsed dict has the expected keys
            assert isinstance(result, dict), "Result is not a dict"
            assert result.get("type") == "service_account", f"type={result.get('type')}"
            assert result.get("project_id") == "telegram-bot-db-aebfb", "project_id mismatch"
            assert "BEGIN" in result.get("private_key", ""), "private_key missing PEM header"

            print(f"         ✅ PASSED  (keys: {len(result)})")
            passed += 1
        except Exception as e:
            # Only show first line of error
            err_line = str(e).split("\n")[0]
            print(f"         ❌ FAILED  — {err_line}")
            failed += 1
        print()

    # Summary
    print("=" * width)
    total = passed + failed
    if failed == 0:
        print(f"  🎉 ALL {total} TESTS PASSED")
    else:
        print(f"  ⚠️  {passed}/{total} passed, {failed} FAILED")
    print("=" * width)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
