#!/usr/bin/env python3
"""
scripts/auto_flezen_cookie.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Fully automated Flezen account generator & verified cookie extractor.
Creates a disposable mailbox via mail.tm, registers on Flezen, confirms the email,
completes user onboarding, and outputs valid, verified session cookies.
"""

import logging
import random
import re
import string
import time
from typing import Optional, Tuple
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def generate_verified_flezen_cookie() -> Optional[Tuple[str, str]]:
    """
    Generate and verify a fresh Flezen account.
    Returns: (cookie_header_string, account_email)
    """
    # 1. Fetch available mail.tm domain
    try:
        dom_res = requests.get("https://api.mail.tm/domains", timeout=10)
        domains = [d["domain"] for d in dom_res.json().get("hydra:member", []) if d.get("isActive")]
        if not domains:
            log.error("No active mail.tm domains available.")
            return None
        domain = domains[0]
    except Exception as e:
        log.error(f"Failed to fetch mail.tm domains: {e}")
        return None

    # 2. Create mailbox
    rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"flezen_bot_{rand_id}@{domain}"
    password = "P@ssw0rd_" + "".join(random.choices(string.digits, k=6))

    log.info(f"1. Creating disposable inbox: {email}")
    acc_res = requests.post("https://api.mail.tm/accounts", json={"address": email, "password": password}, timeout=10)
    if acc_res.status_code != 201:
        log.error(f"Failed to create mail.tm account: {acc_res.text}")
        return None

    tok_res = requests.post("https://api.mail.tm/token", json={"address": email, "password": password}, timeout=10)
    mail_token = tok_res.json().get("token")
    mail_headers = {"Authorization": f"Bearer {mail_token}"}

    # 3. Register on Flezen
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": "https://flezen.com/auth/register",
        "Origin": "https://flezen.com",
    })

    log.info(f"2. Submitting registration on Flezen for {email}...")
    reg_res = session.post("https://flezen.com/auth/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
    }, timeout=15)

    if reg_res.status_code != 200:
        log.error(f"Flezen registration failed: {reg_res.status_code}")
        return None

    # 4. Poll for verification email
    log.info("3. Polling for verification email...")
    msg_id = None
    for _ in range(15):
        time.sleep(3)
        r = requests.get("https://api.mail.tm/messages", headers=mail_headers, timeout=10)
        if r.status_code == 200:
            msgs = r.json().get("hydra:member", [])
            if msgs:
                msg_id = msgs[0]["id"]
                log.info(f"   Received email: '{msgs[0].get('subject')}'")
                break

    if not msg_id:
        log.error("Timed out waiting for verification email.")
        return None

    msg_res = requests.get(f"https://api.mail.tm/messages/{msg_id}", headers=mail_headers, timeout=10)
    body = msg_res.json().get("text", "") or msg_res.json().get("html", "")

    m = re.search(r"https?://flezen\.com/auth/verify\?token=([a-zA-Z0-9_-]+)", body)
    if not m:
        log.error("Could not find verification token in email body.")
        return None

    verify_url = m.group(0)
    log.info(f"4. Confirming email via: {verify_url}")
    session.get(verify_url, allow_redirects=True, timeout=15)

    # 5. Complete Onboarding
    log.info("5. Completing account onboarding...")
    onboard_data = {
        "first_name": "Antigravity",
        "last_name": "Bot",
        "display_name": f"AgyBot_{rand_id}",
        "ref_code": "",
        "traffic_sources": "https://t.me/flezencloud",
    }
    session.headers.update({
        "Referer": "https://flezen.com/user/onboard",
        "Origin": "https://flezen.com",
    })
    session.post("https://flezen.com/user/onboard", data=onboard_data, allow_redirects=False, timeout=15)

    # 6. Extract Final Verified Cookie String
    cookies = session.cookies.get_dict()
    cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    log.info(f"🎉 Successfully generated verified Flezen cookie!")
    log.info(f"   Email: {email}")
    log.info(f"   Cookie: {cookie_str}")

    return cookie_str, email


if __name__ == "__main__":
    res = generate_verified_flezen_cookie()
    if res:
        cookie_str, email = res
        print("\n" + "="*60)
        print("FLEZEN_COOKIE=" + cookie_str)
        print("="*60)
