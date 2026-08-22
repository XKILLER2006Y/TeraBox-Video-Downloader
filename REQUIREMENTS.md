# REQUIREMENTS.md

Functional and non-functional requirements for the TeraBox/Diskwala Telegram Bot.

## Functional Requirements

### FR-1: TeraBox Video Download
- **FR-1.1:** Accept TeraBox share URLs in messages (auto-detect mode) or via `/exp`, `/exphd`, `/get` commands
- **FR-1.2:** Support TeraBox mirror domains: `terabox.com`, `1024terabox.com`, `teraboxapp.com`, `freeterabox.com`, `terabox.app`, `terabox.fun`, `4funbox.co`, `4funbox.com`, `mirrobox.com`, `nephobox.com`, `1024tera.com`, `momerybox.com`, `tibibox.com`
- **FR-1.3:** Extract SURL from path (`/s/1ABC`) or query param (`?surl=1ABC`) formats
- **FR-1.4:** Direct resolution via jsToken extraction + HLS chunk discovery
- **FR-1.5:** Proxy fallback when direct resolution fails
- **FR-1.6:** HD mode via direct download link (requires premium cookies)

### FR-2: Diskwala Video Download
- **FR-2.1:** Accept Diskwala share URLs in messages or via `/dw` command
- **FR-2.2:** Direct resolution via Telethon Mini App auth token + Diskwala API
- **FR-2.3:** Proxy fallback when SESSION is not configured
- **FR-2.4:** Status polling with timeout (120s max)

### FR-3: User Preferences
- **FR-3.1:** Per-user download mode (`get`/`exp`/`exphd`/`dw`) stored in Firestore
- **FR-3.2:** `/settings` command to view and change mode
- **FR-3.3:** Default mode: `exp` for new users

### FR-4: Video Caching
- **FR-4.1:** Downloaded videos uploaded to Telegram storage group
- **FR-4.2:** Cache lookup by SURL/link ID before re-downloading
- **FR-4.3:** Mode-specific cache buckets with priority search
- **FR-4.4:** `/random` command returns random cached video

### FR-5: Download Management
- **FR-5.1:** Cancel button on all download messages
- **FR-5.2:** Progress updates during download and upload
- **FR-5.3:** Timing stats in final caption (download time, upload time, total)

### FR-6: Admin Features
- **FR-6.1:** `/recent` — list users active in last 24h
- **FR-6.2:** `/broadcast` — send message to all users
- **FR-6.3:** `/op` — user feedback to admin

### FR-7: Error Handling
- **FR-7.1:** User-friendly error messages for common TeraBox errors (14 errno codes mapped)
- **FR-7.2:** Graceful handling of expired links, rate limits, geo-blocks
- **FR-7.3:** Wrong-source hints (Diskwala link in TeraBox mode and vice versa)

## Non-Functional Requirements

### NFR-1: Performance
- **NFR-1.1:** Max 20 concurrent downloads (semaphore limit)
- **NFR-1.2:** Multi-part parallel download (4 byte-range connections)
- **NFR-1.3:** Cache lookup < 100ms (Firestore single-document read)
- **NFR-1.4:** HLS chunk discovery completes in < 60s (100 retries max)

### NFR-2: Reliability
- **NFR-2.1:** Auto-retry on transport errors (4 attempts with exponential backoff)
- **NFR-2.2:** Flood-wait auto-queue (no user intervention needed)
- **NFR-2.3:** File cleanup on failure (temp files removed)
- **NFR-2.4:** Firebase errors logged but never crash the bot

### NFR-3: Scalability
- **NFR-3.1:** Firestore per-document user storage (no single-document bottleneck)
- **NFR-3.2:** In-memory cache for `/random` (15-min TTL, reduces Firestore reads)
- **NFR-3.3:** User tracking debounced to 15-min intervals

### NFR-4: Security
- **NFR-4.1:** No secrets in source code (all via `.env`)
- **NFR-4.2:** Firebase service account via env var (base64 or JSON)
- **NFR-4.3:** API keys sent as headers, not URLs

### NFR-5: Deployment
- **NFR-5.1:** Docker image based on `python:3.12-slim`
- **NFR-5.2:** Health check endpoint (`/ping` → `pong`)
- **NFR-5.3:** Persistent storage via Docker volume
- **NFR-5.4:** `restart: unless-stopped` policy

### NFR-6: Maintainability
- **NFR-6.1:** Modular structure (resolver, handler, database separated)
- **NFR-6.2:** Logging at INFO level (structured format)
- **NFR-6.3:** Type hints on public functions
- **NFR-6.4:** No external dependencies beyond requirements.txt
