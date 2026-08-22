# ROADMAP.md

Planned features and improvements for the TeraBox/Diskwala Telegram Bot.

## Phase 1: Current State (Done)

- [x] TeraBox direct resolution (HLS chunk discovery)
- [x] TeraBox proxy fallback (`/get` mode)
- [x] Diskwala direct resolution (Telethon Mini App)
- [x] Diskwala proxy fallback
- [x] Firestore-backed cache and user tracking
- [x] Cancel button on downloads
- [x] Flood-wait auto-queue
- [x] Multi-part parallel downloads
- [x] HLS to MP4 remuxing
- [x] Docker deployment
- [x] Error handling with user-friendly messages
- [x] End-to-end tests

## Phase 2: Reliability & Polish

- [ ] **Session auto-renewal** — Detect expired Telethon sessions, prompt admin to regenerate
- [ ] **Cookie rotation** — Automatically cycle through `COOKIES1..N` on 403/rate-limit
- [ ] **Retry budget** — Per-user retry limits to prevent abuse
- [ ] **Health dashboard** — `/status` command showing cache stats, active downloads, error rates
- [ ] **Structured logging** — JSON logs for easier debugging in production
- [ ] **Graceful shutdown** — Finish in-progress downloads before stopping

## Phase 3: Features

- [ ] **Batch download** — Send multiple URLs in one message, process sequentially
- [ ] **Quality selection** — `/exp 720p` or `/exp 1080p` to choose resolution
- [ ] **Subtitles** — Download and attach subtitle files when available
- [ ] **Thumbnail preview** — Show video thumbnail before download
- [ ] **File size limit** — Warn or skip files over configurable threshold
- [ ] **Playlist support** — Detect and download entire TeraBox folders
- [ ] **Diskwala folder support** — Resolve folder shares (currently single-file only)

## Phase 4: Performance

- [ ] **Connection pooling** — Reuse HTTP sessions across downloads
- [ ] **Segment parallelism** — Download HLS segments in parallel (currently sequential)
- [ ] **Background upload** — Start uploading while downloading remaining segments
- [ ] **Cache warming** — Pre-download popular/trending videos
- [ ] **CDN optimization** — Test and select fastest TeraBox CDN endpoint

## Phase 5: User Experience

- [ ] **Inline mode** — `@bot inline` to search and send cached videos
- [ ] **Language support** — i18n for Hindi, English, Marathi
- [ ] **Custom thumbnails** — Users can set preferred thumbnail for downloads
- [ ] **Download history** — `/history` command showing user's past downloads
- [ ] **Rate limiting** — Per-user download limits (configurable by admin)
- [ ] **Announcements** — `/announce` to push update notifications to users

## Phase 6: Infrastructure

- [ ] **Monitoring** — Prometheus metrics endpoint
- [ ] **Alerting** — Telegram notifications for errors/downtime
- [ ] **Backup** — Automated Firestore export
- [ ] **CI/CD** — GitHub Actions for tests + Docker build
- [ ] **Staging environment** — Separate bot for testing changes
- [ ] **Load testing** — Simulate 100+ concurrent users

## Technical Debt

- [ ] Remove legacy `terabox/` module (proxy-only, replaced by `teraboxDL/`)
- [ ] Remove Pyrogram remnants (any leftover imports/references)
- [ ] Standardize error types (currently `TeraBoxError`, `TeraBoxDirectError`, `DiskwalaError`, `DiskwalaDirectError`)
- [ ] Add type hints to all functions
- [ ] Increase test coverage to 90%+
- [ ] Document all environment variables in `.env.example`

## Future Ideas

- **Web panel** — Admin dashboard for managing users, viewing logs, rotating cookies
- **Multi-bot support** — Run multiple bot instances with different tokens
- **Plugin system** — Allow adding new cloud services (Google Drive, Mediafire, etc.)
- **AI-powered URL detection** — Auto-detect URL type without regex matching
- **Webhook mode** — Alternative to long-polling for serverless deployment
