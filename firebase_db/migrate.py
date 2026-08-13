import os
import sys

# Ensure the parent directory is in sys.path to allow importing from firebase_db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firebase_db.db import db

def migrate_user_modes():
    print("Starting migration: Updating users with mode 'get' to 'exp'...")
    users_ref = db.collection("users")
    
    # Query all users where mode == 'get'
    # Use filter instead of kwargs based on standard Firestore Python SDK practices
    query = users_ref.where("mode", "==", "get").stream()
    
    total_updated = 0
    batch = db.batch()
    batch_count = 0
    
    for doc in query:
        doc_ref = users_ref.document(doc.id)
        batch.update(doc_ref, {"mode": "exp"})
        total_updated += 1
        batch_count += 1
        
        # Firestore batch limit is 500 operations
        if batch_count >= 490:
            batch.commit()
            print(f"Committed batch of {batch_count} updates...")
            batch = db.batch()
            batch_count = 0
            
    # Commit the remaining documents
    if batch_count > 0:
        batch.commit()
        print(f"Committed final batch of {batch_count} updates...")
        
    print(f"Migration complete! A total of {total_updated} users were updated.")

if __name__ == "__main__":
    migrate_user_modes()
