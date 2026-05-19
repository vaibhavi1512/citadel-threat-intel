# CITADEL Exposure Intelligence Platform — Architecture Overview

This document is a **read-only** map of the repository as implemented today. Paths are **repo-relative** from the workspace root (for example: `backend/main.py`).

## Repository map (what lives where)

- **Backend API (FastAPI)**: `backend/main.py`
  - Global singletons: `ThreatIntelligenceEngine engine`, `MonitoringEventBus event_bus`, `MonitoringScheduler scheduler`
- **Intelligence engine / NLP pipeline**: `utils/nlp_engine.py` (`ThreatIntelligenceEngine`)
- **Public-source collectors + aggregation**: `utils/source_intel_service.py` (`ExternalIntelligenceService`, per-source clients)
- **Signal quality + stable fingerprints**: `utils/signal_quality.py`
- **Enrichment helpers (entities, correlation helpers, multilingual)**: `utils/intel_enrichment.py`
- **Monitoring scheduler + SSE bus**: `utils/monitoring_runtime.py` (`MonitoringScheduler`, `MonitoringEventBus`)
- **Persistence facade**: `utils/db.py` (`MongoManager`)
- **Durable local JSON store (cases/watchlists/audit/signed reports)**: `utils/local_store.py` (`LocalMonitoringStore`)
- **Case normalization schema (Pydantic)**: `utils/case_schema.py`
- **PDF report generation**: `utils/reporting.py`
- **Cyber cell reporting orchestration**: `services/cyber_cell_reporting/__init__.py` (+ helpers under `services/cyber_cell_reporting/`)
- **Signed report record creation + public verification helpers**: `services/signed_reports.py`
- **Verification response cache (TTL)**: `services/report_verification_cache.py` (`verification_response_cache`)
- **Cryptographic signing + URL building**: `security/report_signing/` (notably `signing.py`, `verification.py`, `hashing.py`)
- **Configuration + env vars**: `utils/config.py`
- **Primary UI (React + Vite)**: `frontend-react/src/` (router: `frontend-react/src/App.jsx`, API client: `frontend-react/src/services/api.js`)

## System components (runtime view)

### Frontend (`frontend-react/`)

- **SPA** served by Vite during development; calls FastAPI via Axios (`frontend-react/src/services/api.js`).
- **Live monitoring UI** uses browser `EventSource` against `GET /events/stream` (implemented in `frontend-react/src/pages/Feed.jsx`).

### Backend (`backend/main.py`)

- **FastAPI app** `app` with CORS enabled for all origins and selected response headers exposed for PDF exports.
- **Startup/shutdown**:
  - Startup: `ThreatIntelligenceEngine.bootstrap()` then `MonitoringScheduler.start()`
  - Shutdown: `MonitoringScheduler.stop()`

### Monitoring + pipeline

- **Scheduler thread**: `MonitoringScheduler._run_loop` in `utils/monitoring_runtime.py`
  - Every ~5 seconds: list enabled watchlists, run due watchlists, persist scheduler summary, expire signed reports.
- **Watchlist execution**: `MonitoringScheduler.run_watchlist` → `ThreatIntelligenceEngine.sync_watchlist` → case persistence + SSE events + optional webhook POST.

### Storage (two different persistence layers)

1. **MongoDB (optional) — “alerts / analyses”**
   - Written by `MongoManager.insert_analysis` in `utils/db.py` when `MONGO_ENABLED` is true and Mongo is reachable.
   - Read by `MongoManager.fetch_alerts` for `/alerts` and parts of `/stats`.

2. **Local JSON file — “monitoring domain state”**
   - Path: `utils/config.py` → `MONITORING_STATE_PATH` (default `data/monitoring_state.json`)
   - Implemented by `LocalMonitoringStore` in `utils/local_store.py`
   - Stores: `cases`, `watchlists`, `audit_events`, `signed_reports`, embedded `scheduler` snapshot fields, plus local `alerts` fallback when Mongo is unavailable/disabled.

**Important debugging reality:** `MongoManager` delegates **cases/watchlists/audit/signed reports** to `LocalMonitoringStore` even when Mongo is connected. Mongo is primarily for the **analysis alert collection** configured by `MONGO_DB_NAME` / `MONGO_COLLECTION`.

### SSE (Server-Sent Events)

- **Publisher**: `MonitoringEventBus.publish` serializes dict payloads to JSON strings and fans out to subscriber queues (`utils/monitoring_runtime.py`).
- **HTTP stream**: `GET /events/stream` in `backend/main.py` (`stream_events`) yields `data: <json>\n\n` and periodic heartbeats.

### Export system (JSON snapshot + PDF)

- **JSON export**: `GET /cases/export` → `MongoManager.export_monitoring_snapshot` → `LocalMonitoringStore.export_snapshot`
- **PDF export**: `GET /export/report/pdf` → `utils/reporting.generate_pdf_report` + `services/signed_reports.create_signed_report_record`

## Main operational flows (end-to-end)

### 1) Monitoring → ingestion → enrichment → case creation

1. **Scheduler tick** (`MonitoringScheduler._run_loop` in `utils/monitoring_runtime.py`)
   - Loads enabled watchlists via `MongoManager.list_watchlists` → `LocalMonitoringStore.list_watchlists`
2. **Watchlist sync** (`ThreatIntelligenceEngine.sync_watchlist` in `utils/nlp_engine.py`)
   - Calls `ThreatIntelligenceEngine.collect_external_intelligence(..., persist=False)` to build normalized “finding results” without persisting intermediate alerts as cases.
   - Recomputes org relevance using watchlist-aware profile resolution (`resolve_organization_profile`)
   - Computes case eligibility via `assess_correlation` (`intelligence/correlation/__init__.py`)
   - For eligible rows: `ThreatIntelligenceEngine._build_exposure_case` then `MongoManager.save_case` → `LocalMonitoringStore.save_case`
3. **SSE fanout** (`MonitoringScheduler._emit_case_events`)
   - Publishes `event_type: case_updated` payloads to `MonitoringEventBus`
4. **Frontend refresh** (`frontend-react/src/pages/Feed.jsx`)
   - On `case_updated`, calls `loadMonitoring()` which refetches `/monitoring/stats`, `/cases`, `/watchlists`, `/audit-events`, and `/cases/{id}`.

### 2) Manual collection (operator-driven)

- `POST /collect-intel` (`collect_intelligence` in `backend/main.py`)
- Uses `ThreatIntelligenceEngine.collect_external_intelligence` with `persist` controlled by the request body.
- When cases are created/merged, `backend/main.py` publishes `case_updated` SSE events (`trigger: manual_collect`).

### 3) Case updates → SSE

- `PATCH /cases/{case_id}` (`update_case` in `backend/main.py`)
  - Persists via `MongoManager.update_case` → `LocalMonitoringStore.update_case`
  - Publishes `case_updated` with `action: manual_update`

### 4) PDF export → signed report record → verification surface

1. `GET /export/report/pdf` (`export_pdf_report` in `backend/main.py`)
2. `utils/reporting.generate_pdf_report` writes a PDF under the OS temp directory (`utils/reporting.py`)
3. `services/signed_reports.create_signed_report_record` hashes PDF bytes, signs a canonical JSON payload (`security/report_signing`), persists a `signed_reports[]` entry via `MongoManager.save_signed_report`
4. Response includes headers: `X-Citadel-Report-Id`, `X-Citadel-Verification-Url`, `X-Citadel-Signature-Status` (see `backend/main.py` CORS `expose_headers` too)
5. Verification UI loads metadata via `GET /api/v1/verify/report/{report_id}` and can upload a PDF to `POST /api/v1/verify/report/{report_id}/upload`

### 5) Cyber cell reporting → email → audit trail → SSE

1. `POST /api/v1/report/cybercell/preview` → `services/cyber_cell_reporting.build_preview`
   - Validates selection (`eligibility_validator.validate_case_selection`)
   - Generates PDF + complaint + optional JSON bundle attachment metadata
   - Creates signed report record (`create_signed_report_record`)
   - Writes preview audit (`services/cyber_cell_reporting/audit_logger.record_preview_audit`)
2. `POST /api/v1/report/cybercell/send` → `services/cyber_cell_reporting.send_report`
   - Requires `confirmation_flag: true` and a valid `preview_id` validated by `preview_store.validate`
   - Sends email via `send_cyber_cell_email` (`services/cyber_cell_reporting/email_sender.py`) unless mock/disabled per env vars
   - Writes send audit (`record_send_audit`) and updates signed report status to `sent` when appropriate
3. `backend/main.py` publishes `cyber_cell_report_sent` / `cyber_cell_report_failed` events consumed by `Feed.jsx`

## Data stores: what they store

### `data/monitoring_state.json` (via `LocalMonitoringStore`)

Top-level keys (`LocalMonitoringStore._default_state` in `utils/local_store.py`):

- **`cases`**: normalized exposure cases (`utils/case_schema.normalize_case_list` on load; `normalize_case_record` on write/read paths)
- **`watchlists`**: monitoring definitions (query, interval, enabled, assets/tags, webhook URL, run metadata)
- **`audit_events`**: append-only operational audit stream (cap **500**)
- **`signed_reports`**: CVRP-style signed report metadata records (cap enforced indirectly via list growth; sorted on write)
- **`scheduler`**: last tick + last cycle summary dict (`update_scheduler_state`)
- **`alerts`**: local fallback store for “analysis-like” records when Mongo path is not used

### MongoDB (optional)

- Database: `MONGO_DB_NAME` (default `dark_web_threat_intel`)
- Collection: `MONGO_COLLECTION` (default `analyses`)
- Document shape: compatible with legacy dashboard expectations; external intel results are stored under `results` similarly to `ThreatIntelligenceEngine.analyze_text`.

## Cross-links (deep dives)

- Backend route-by-route details: `docs/backend_deep_dive.md`
- Schema field guide: `docs/data_models.md`
- Pipeline trace with pseudo payloads: `docs/pipeline_trace.md`
- Debugging: `docs/debugging_playbook.md`
- Frontend: `docs/frontend_deep_dive.md`
- Sequence diagrams: `docs/sequence_diagrams.md`
