# E2E Test Infra: TeraBox Video Downloader Bot

## Test Philosophy
- Opaque-box, requirement-driven testing based on `ORIGINAL_REQUEST.md`.
- No reliance on internal implementation details; exercise components via public contracts, APIs, CLI, and unit/integration interfaces.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing.

## Feature Inventory
| # | Feature | Source (Requirement) | Tier 1 (Coverage) | Tier 2 (Boundaries) | Tier 3 (Pairwise) | Tier 4 (Real-World) |
|---|---------|----------------------|:-----------------:|:-------------------:|:-----------------:|:-------------------:|
| 1 | Multi-Part Zero-Copy Downloader | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| 2 | HLS Remuxing & Chunk Streaming | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| 3 | Downloader Engines (`teraboxDL`, `flareDL`, `flezenDL`, `diskwalaDL`, `universalDL`, `social_dl`) | ORIGINAL_REQUEST §R1 | 6 | 6 | ✓ | ✓ |
| 4 | Token Resolution & Failovers | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| 5 | Network & Socket Connection Pooling | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| 6 | FastTelethon Parallel Uploads | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| 7 | Storage GC & Active File Protection | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| 8 | Telegram Commands (15 Commands) | ORIGINAL_REQUEST §R3 | 15 | 10 | ✓ | ✓ |
| 9 | Health Endpoints (`/health`, `/ping`, `/api/stats`, `/dash`) | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |
| 10 | Error Hardening & User Feedback | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Test Runner**: `pytest` / `python3 -m unittest`
- **Invocation**: `pytest -v tests/`
- **Pass/Fail Semantics**: 100% test pass rate, 0 collection errors, exit code 0.
- **Directory Layout**:
  - `tests/test_enhancements.py`: Memory, cache, and token resilience tests.
  - `tests/test_social_media.py`: Social media downloader extraction tests.
  - `tests/test_flare.py`: Flare downloader integration and error tests.
  - `tests/test_flezen.py`: Flezen downloader integration and error tests.
  - `tests/test_terabox_zerocopy.py`: Zero-copy multipart and HLS pipeline tests.
  - `tests/test_fast_upload.py`: Concurrency bounding, monotonic progress, and retry tests.
  - `tests/test_storage_gc.py`: Active file protection and orphan cleanup tests.
  - `tests/test_commands.py`: All 15 Telegram bot command handler tests.
  - `tests/test_web_endpoints.py`: FastAPI endpoints (`/health`, `/ping`, `/api/stats`, `/dash`).
  - `tests/test_e2e_scenarios.py`: Multi-engine end-to-end download, remux, upload, and error scenarios.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | High-Volume Concurrent Video Ingestion | Zero-copy download + connection pool + fast upload | High |
| 2 | Long-Running Transfer with Background GC | 15-minute mock transfer + storage GC tick + orphan cleanup | High |
| 3 | Upstream Mirror Failover & Token Rotation | Rate limit 429 + cookie failover + cache fallback | Medium |
| 4 | Multi-Format Audio/Video Command Pipeline | `/dl` + `/mp3` universal routing + progress callbacks | Medium |
| 5 | Web Health Monitoring Under Load | `/health` + `/api/stats` concurrent queries during transfers | Medium |

## Coverage Thresholds
- Tier 1: ≥5 per feature (Total ≥ 50 cases)
- Tier 2: ≥5 per feature boundary (Total ≥ 50 cases)
- Tier 3: Pairwise coverage of engine-network-upload combinations (Total ≥ 15 cases)
- Tier 4: ≥5 realistic end-to-end application scenarios
