import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Initialize Firebase Admin SDK (only once) ──────────────────────────────────
# Reads FIREBASE_SECRETS env-var → writes fb_secrets.json → loads from file.
# This avoids all \n / PEM-escaping issues that plague in-memory dict loading.

def _init_firebase() -> firestore.Client:
    if firebase_admin._apps:
        # Already initialized (e.g. during hot-reload / testing)
        return firestore.client()

    module_dir = os.path.dirname(__file__)
    generated_path = os.path.join(module_dir, "fb_secrets.json")

    firebase_secrets_json = os.getenv("FIREBASE_SECRETS")

    if firebase_secrets_json:
        try:
            # Strip accidental surrounding quotes that might have been copy-pasted
            # into the Render environment variables dashboard
            clean_json_str = firebase_secrets_json.strip().strip("'").strip('"')
            
            # Parse the env-var JSON and write it out as a proper file.
            # credentials.Certificate(path) handles PEM parsing itself.
            secrets_dict = json.loads(clean_json_str)
            with open(generated_path, "w", encoding="utf-8") as f:
                json.dump(secrets_dict, f, indent=2)
            log.info(f"Wrote credentials to {generated_path}")

            cred = credentials.Certificate(generated_path)
            firebase_admin.initialize_app(cred)
            log.info("Firebase initialized from FIREBASE_SECRETS env-var (via file).")
        except Exception as e:
            log.error(f"Failed to parse FIREBASE_SECRETS env-var: {e}")
            raise
    elif os.path.exists(generated_path):
        # Previously generated file still around
        cred = credentials.Certificate(generated_path)
        firebase_admin.initialize_app(cred)
        log.info(f"Firebase initialized from previously generated: {generated_path}")
    else:
        raise RuntimeError(
            "No Firebase credentials found. "
            "Set FIREBASE_SECRETS env-var in .env."
        )

    return firestore.client()


db: firestore.Client = _init_firebase()
