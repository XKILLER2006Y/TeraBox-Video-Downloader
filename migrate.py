import json
import time
import os
from firebase_db.cache import _encode_key
from firebase_db.db import get_db
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Standalone script — safe to init Firebase at import time.
db = get_db()

def migrate_users():
    users_file = "users.json"
    if not os.path.exists(users_file):
        logger.warning(f"{users_file} not found. Skipping user migration.")
        return

    logger.info(f"Starting migration of users from {users_file}...")
    try:
        with open(users_file, "r") as f:
            users_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {users_file}: {e}")
        return

    total_users = len(users_data)
    logger.info(f"Found {total_users} users to migrate.")

    collection_ref = db.collection("users")

    # Delete all existing users first
    logger.info("Deleting all existing users from Firestore...")
    docs = collection_ref.stream()
    delete_batch = db.batch()
    delete_count = 0
    for doc in docs:
        delete_batch.delete(doc.reference)
        delete_count += 1
        if delete_count % 400 == 0:
            delete_batch.commit()
            logger.info(f"Deleted {delete_count} existing users...")
            delete_batch = db.batch()
            time.sleep(0.5)
    
    if delete_count % 400 != 0:
        delete_batch.commit()
    logger.info(f"Total deleted users: {delete_count}")

    # Push new users
    batch = db.batch()
    count = 0
    batch_count = 0

    for user_id_str, user_info in users_data.items():
        doc_ref = collection_ref.document(user_id_str)
        batch.set(doc_ref, user_info)
        count += 1

        # Commit every 400 operations (Firestore limit is 500)
        if count % 400 == 0:
            batch.commit()
            batch_count += 1
            logger.info(f"Committed batch {batch_count} of users ({count}/{total_users})")
            time.sleep(1) # Small delay to respect rate limits
            batch = db.batch()

    # Commit any remaining
    if count % 400 != 0:
        batch.commit()
        logger.info(f"Committed final batch of users ({count}/{total_users})")

    logger.info("User migration completed successfully.")

def migrate_cache():
    cache_file = "cache.json"
    if not os.path.exists(cache_file):
        logger.warning(f"{cache_file} not found. Skipping cache migration.")
        return

    logger.info(f"Starting migration of cache from {cache_file}...")
    try:
        with open(cache_file, "r") as f:
            cache_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {cache_file}: {e}")
        return

    collection_ref = db.collection("cache")
    
    # Cache migration strategy: 
    # The cache collections are single documents per bucket ('get', 'exp', 'exphd')
    # If the number of keys is very large, updating a single document with thousands 
    # of fields in one go might hit limits. It's safer to update the document in chunks.

    for bucket_name, bucket_data in cache_data.items():
        total_items = len(bucket_data)
        logger.info(f"Migrating bucket '{bucket_name}' with {total_items} items.")

        doc_ref = collection_ref.document(bucket_name)

        # Delete the existing document first so stale / wrongly-encoded keys
        # from any previous migration run don't persist alongside the new ones.
        if doc_ref.get().exists:
            doc_ref.delete()
            logger.info(f"  Deleted existing Firestore document for bucket '{bucket_name}'")
            time.sleep(0.5)

        # Write the fresh data in chunks (set without merge=True after the delete)
        chunk_size = 300  # Number of fields to write per request
        items = list(bucket_data.items())
        
        for i in range(0, total_items, chunk_size):
            chunk = dict(items[i:i + chunk_size])
            safe_chunk = {_encode_key(k): v for k, v in chunk.items()}
            
            try:
                doc_ref.set(safe_chunk, merge=True)
                logger.info(f"  Updated chunk {i} to {min(i + chunk_size, total_items)} in bucket '{bucket_name}'")
                time.sleep(1) # Sleep to respect rate limits
            except Exception as e:
                logger.error(f"  Failed to update chunk in bucket '{bucket_name}': {e}")

    logger.info("Cache migration completed successfully.")

if __name__ == "__main__":
    logger.info("--- Starting Migration Process ---")
    migrate_users()
    # logger.info("----------------------------------")
    # migrate_cache()
    # logger.info("--- Migration Process Finished ---")
