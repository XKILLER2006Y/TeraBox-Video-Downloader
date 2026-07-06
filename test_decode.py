"""
Test script for _decode_env_json + _fix_private_key
Simulates every mangled env-var format Docker / Coolify / docker-compose can produce.

Run:  python test_decode.py
      python -X utf8 test_decode.py    (on Windows if encoding errors)
"""

import re
import json
import base64
import logging

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ─── Copy of _decode_env_json from firebase_db/db.py ─────────────────────────

def _decode_env_json(raw: str) -> dict:
    """Try progressively more aggressive strategies to parse *raw* into a dict."""

    cleaned = raw.strip().lstrip("\ufeff").strip()

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

    if '\\"' in cleaned or "\\'" in cleaned:
        cleaned = cleaned.replace('\\"', '"').replace("\\'", "'")

    # 2. Plain json.loads
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Un-double-escape
    try:
        unescaped = cleaned.replace('\\"', '"')
        result = json.loads(unescaped)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 4. Fix literal \\n
    try:
        fixed = cleaned.replace("\\\\n", "\n").replace("\\n", "\n")
        result = json.loads(fixed)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 5. Combined
    try:
        combined = cleaned.replace('\\"', '"').replace("\\\\n", "\n").replace("\\n", "\n")
        result = json.loads(combined)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 6. Base64
    try:
        decoded_bytes = base64.b64decode(cleaned, validate=True)
        result = json.loads(decoded_bytes.decode("utf-8"))
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # 7. Python dict
    try:
        import ast
        result = ast.literal_eval(cleaned)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # 8. Regex extract
    try:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
            if isinstance(result, dict):
                return result
    except (json.JSONDecodeError, ValueError):
        pass

    raise ValueError(
        f"Could not decode into a JSON dict.\n"
        f"  Length : {len(raw)} chars\n"
        f"  Starts: {repr(raw[:30])}\n"
        f"  Ends  : {repr(raw[-30:])}"
    )


# ─── Copy of _fix_private_key from firebase_db/db.py ────────────────────────

def _fix_private_key(creds: dict) -> None:
    """Fix PEM private_key that has literal '\\n' instead of real newlines."""
    pk = creds.get("private_key")
    if not pk or "\n" in pk:
        return
    fixed = pk.replace("\\n", "\n").replace("\\\\n", "\n")
    if not fixed.endswith("\n"):
        fixed += "\n"
    creds["private_key"] = fixed
    log.debug("Fixed private_key: replaced literal \\n with real newlines.")


# ─── Fake service-account JSON ───────────────────────────────────────────────

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


# ─── Test cases ──────────────────────────────────────────────────────────────

def build_test_cases():
    cases = []

    # 1. Clean JSON
    cases.append(("Clean JSON", CLEAN_JSON))

    # 2. Single-quoted wrapper
    cases.append(("Single-quoted wrapper", f"'{CLEAN_JSON}'"))

    # 3. Double-quoted wrapper
    cases.append(("Double-quoted wrapper", f'"{CLEAN_JSON}"'))

    # 4. COOLIFY EXACT FORMAT:  \'...\' with \" inside
    escaped_internals = CLEAN_JSON.replace('"', '\\"')
    coolify_format = f"\\'{escaped_internals}\\'"
    cases.append(("Coolify \\' + \\\" (1st error format)", coolify_format))

    # 5. Double-escaped quotes only
    cases.append(("Double-escaped quotes (no wrapper)", escaped_internals))

    # 6. Double-quoted + double-escaped
    cases.append(("Double-quoted + escaped internals", f'"{escaped_internals}"'))

    # 7. Escaped newlines in private_key
    escaped_newlines = CLEAN_JSON.replace("\n", "\\n")
    cases.append(("Escaped newlines (\\\\n)", escaped_newlines))

    # 8. Escaped quotes + escaped newlines
    combined_mangled = CLEAN_JSON.replace('"', '\\"').replace("\n", "\\n")
    cases.append(("Escaped quotes + escaped newlines", combined_mangled))

    # 9. Base64 encoded
    b64 = base64.b64encode(CLEAN_JSON.encode("utf-8")).decode("ascii")
    cases.append(("Base64 encoded", b64))

    # 10. Python dict (single quotes)
    py_dict = str(SAMPLE_DICT)
    cases.append(("Python dict (single quotes)", py_dict))

    # 11. BOM prefix
    cases.append(("UTF-8 BOM prefix", "\ufeff" + CLEAN_JSON))

    # 12. Whitespace + quotes
    cases.append(("Whitespace + quotes", f"  ' {CLEAN_JSON} '  "))

    # 13. Triple-nested quotes
    cases.append(("Triple-nested quotes", f"""'"{CLEAN_JSON}"'"""))

    # 14. Garbage prefix/suffix
    cases.append(("Garbage prefix/suffix", f"EXPORT={CLEAN_JSON};"))

    # 15. COOLIFY FULL PEM BREAKAGE: \' + \" + \\n in private_key
    #     This is the EXACT scenario from the user's 2nd error:
    #     JSON decodes fine but PEM has literal \n instead of real newlines
    pem_mangled = CLEAN_JSON.replace("\n", "\\n").replace('"', '\\"')
    coolify_pem = f"\\'{pem_mangled}\\'"
    cases.append(("Coolify full (\\' + \\\" + \\\\n PEM) -- 2nd error", coolify_pem))

    return cases


# ─── Run ─────────────────────────────────────────────────────────────────────

def main():
    cases = build_test_cases()
    passed = 0
    failed = 0
    width = 60

    print("=" * width)
    print("  _decode_env_json + _fix_private_key  Test Suite")
    print("=" * width)
    print()

    for i, (name, mangled) in enumerate(cases, 1):
        preview = repr(mangled[:70]) + ("..." if len(mangled) > 70 else "")
        print(f"Test {i:2d}: {name}")
        print(f"         Input: {preview}")
        try:
            result = _decode_env_json(mangled)
            _fix_private_key(result)

            # Basic structure checks
            assert isinstance(result, dict), "Result is not a dict"
            assert result.get("type") == "service_account"
            assert result.get("project_id") == "telegram-bot-db-aebfb"

            # PEM checks — the critical part
            pk = result.get("private_key", "")
            assert "BEGIN" in pk, "private_key missing PEM header"
            assert "\n" in pk, "private_key has no real newlines!"
            assert "\\" not in pk, f"private_key still has backslashes: {repr(pk[:60])}"
            assert pk.startswith("-----BEGIN"), f"PEM header mangled: {pk[:30]}"

            print(f"         [PASS]  keys={len(result)}, PEM OK")
            passed += 1
        except Exception as e:
            err_line = str(e).split("\n")[0]
            print(f"         [FAIL]  {err_line}")
            failed += 1
        print()

    print("=" * width)
    total = passed + failed
    if failed == 0:
        print(f"  ALL {total} TESTS PASSED")
    else:
        print(f"  {passed}/{total} passed, {failed} FAILED")
    print("=" * width)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
