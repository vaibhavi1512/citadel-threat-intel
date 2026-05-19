# Backend Deep Dive — FastAPI, Routers, Pipeline, SSE, Reporting

This document explains the backend **as implemented** in this repository, with **exact file paths** and **Python symbols** you can jump to quickly while debugging.

## FastAPI project structure

### Entrypoint / app factory

- **File**: `backend/main.py`
- **App**: `app = FastAPI(...)`
- **Singletons (module scope)**:
  - `engine = ThreatIntelligenceEngine()` (`utils/nlp_engine.py`)
  - `event_bus = MonitoringEventBus()` (`utils/monitoring_runtime.py`)
  - `scheduler = MonitoringScheduler(engine, event_bus)` (`utils/monitoring_runtime.py`)

### Lifecycle hooks

- `@app.on_event("startup")` → `startup_event()`
  - Calls `engine.bootstrap()` (`ThreatIntelligenceEngine.bootstrap`)
  - Calls `scheduler.start()` (`MonitoringScheduler.start`)
- `@app.on_event("shutdown")` → `shutdown_event()`
  - Calls `scheduler.stop()` (`MonitoringScheduler.stop`)

### Middleware

- **CORS**: `CORSMiddleware` configured with `allow_origins=["*"]` and exposes PDF/CVRP headers (see `expose_headers` list in `backend/main.py`).

### Pydantic request models (defined inline in `backend/main.py`)

- `AnalyzeRequest`
- `AnalyzeResponse` (response_model for `/analyze`)
- `CollectIntelRequest`
- `WatchlistRequest`
- `CaseUpdateRequest`

## Authentication / authorization (actual behavior)

There is **no login/JWT/session middleware** in `backend/main.py`.

What exists instead:

- **Open CORS** (`allow_origins=["*"]`) — treat this service as **internal** unless you add a reverse proxy with auth.
- **Optional user attribution header**:
  - `X-User-Id` is read in:
    - `export_pdf_report` (`create_signed_report_record(... created_by_user_id=...)`)
    - `preview_cyber_cell_report` / `send_cyber_cell_report` (`build_preview`, `send_report`)
- **Cyber cell send gate** (not “auth”, but an integrity control):
  - Requires `confirmation_flag: true` and a validated `preview_id` (`services/cyber_cell_reporting.send_report`)

## Persistence layer (how `engine.db` works)

- **Class**: `MongoManager` — `utils/db.py`
- **Constructor**: sets `self.local_store = LocalMonitoringStore(MONITORING_STATE_PATH)` (`utils/config.py` → `data/monitoring_state.json` by default)

### Mongo responsibilities

- `MongoManager.insert_analysis` writes to Mongo **only if** `MONGO_ENABLED` is true **and** ping/insert succeeds.
- `MongoManager.fetch_alerts` reads Mongo when connected; otherwise reads local fallback alerts.

### Always-local responsibilities (even if Mongo is healthy)

Delegated to `LocalMonitoringStore` (`utils/local_store.py`) via `MongoManager` methods:

- Cases: `list_cases`, `get_case`, `save_case`, `update_case`
- Watchlists: `list_watchlists`, `save_watchlist`, `delete_watchlist`, `record_watchlist_run`
- Scheduler snapshot: `update_scheduler_state`
- Audit stream: `record_audit_event`, `list_audit_events`, `count_audit_events`
- Signed reports: `save_signed_report`, `get_signed_report`, `list_signed_reports`, `update_signed_report`, `expire_signed_reports`
- Export snapshot: `export_monitoring_snapshot`

## SSE streaming logic

### Event bus (publisher)

- **File**: `utils/monitoring_runtime.py`
- **Class**: `MonitoringEventBus`

Key methods:

- `publish(payload: dict) -> None`
  - Serializes: `json.dumps({"sent_at": <iso>, **payload})`
  - Keeps a small in-memory history (`_history`, last **50**) for late subscribers
  - Fans out to each subscriber queue via `Queue.put`
- `subscribe() -> Queue[str]`
  - Creates a subscriber queue and replays the last **10** history messages
- `unsubscribe(subscriber)`

### HTTP endpoint (consumer)

- **Route**: `GET /events/stream`
- **Handler**: `stream_events()` in `backend/main.py`
- **Mechanics**:
  - `subscriber = event_bus.subscribe()`
  - Loop: `subscriber.get(timeout=15)` → yields `data: <message>\n\n` where `<message>` is already JSON text from the bus
  - On timeout (`queue.Empty`): yields SSE heartbeat `event: heartbeat\ndata: {}\n\n`
  - `finally`: `event_bus.unsubscribe(subscriber)`

### Event types you should expect (by construction)

These strings appear as `event_type` fields inside the JSON payload published by `backend/main.py` and `utils/monitoring_runtime.py`:

- `case_updated` (watchlist sync, manual collect case updates, manual PATCH case)
- `watchlist_error` (scheduler caught exception while running a watchlist)
- `cyber_cell_report_sent` / `cyber_cell_report_failed` (after `/api/v1/report/cybercell/send` attempts)

## Scheduler / monitor loop

- **File**: `utils/monitoring_runtime.py`
- **Class**: `MonitoringScheduler`

Key methods:

- `start()` / `stop()`
- `_run_loop()`
  - Sleeps via `self._stop_event.wait(5)`
  - Pulls enabled watchlists: `engine.db.list_watchlists(enabled_only=True)`
  - Skips watchlists whose `next_run_at` is still in the future (`record_watchlist_run` sets next run)
  - Calls `run_watchlist(watchlist, trigger="scheduled")` for due items
  - Calls `engine.db.expire_signed_reports(now=...)` and persists scheduler summary via `engine.db.update_scheduler_state`
- `run_watchlist_now(watchlist_id)` → manual trigger path (`trigger="manual"`)
- `run_watchlist(watchlist, trigger=...)`
  - Calls `engine.sync_watchlist(watchlist)` (`utils/nlp_engine.py`)
  - Emits SSE via `_emit_case_events`
  - Optionally POSTs webhook via `_dispatch_webhook_if_configured` (uses `requests.post`)
  - Records watchlist run stats via `engine.db.record_watchlist_run`

Watchlist payload normalization:

- `MonitoringScheduler.normalize_watchlist_payload` (used by `backend/main.py` watchlist create/update routes)

## Intelligence pipeline (engine hub)

- **File**: `utils/nlp_engine.py`
- **Class**: `ThreatIntelligenceEngine`

### Public entrypoints

- `bootstrap()` — ensures ML artifacts via `ModelManager.ensure_models()` (`utils/model_manager.py`)
- `analyze_text(text, persist=True)` — standalone text analysis; persists via `_persist_result_alert` → `MongoManager.insert_analysis`
- `collect_external_intelligence(query, persist=True, demo=False)` — manual/operator collection path used by `POST /collect-intel`
- `sync_watchlist(watchlist)` — scheduled monitoring path used by `MonitoringScheduler`

### Collector layer (before engine “finding result”)

- **File**: `utils/source_intel_service.py`
- **Class**: `ExternalIntelligenceService`
  - `collect(query)` / `build_demo_collection(query)`
  - Emits structured `findings[]` consumed by `_build_external_finding_result`

### Core per-finding pipeline (external intel)

Implemented primarily in `ThreatIntelligenceEngine._build_external_finding_result` (`utils/nlp_engine.py`):

1. **Normalize text** via multilingual + slang + cleaning:
   - `normalize_multilingual_text` (`utils/intel_enrichment.py`)
   - `decode_slang` (`utils/intel_enrichment.py`)
   - `clean_text` (`utils/text_utils.py`)
2. **Pattern extraction** via `detect_patterns` (`REGEX_PATTERNS` in `utils/nlp_engine.py`) + `filter_pattern_matches` (`intelligence/validators`)
3. **Entity extraction**:
   - spaCy NER via `extract_entities` (may return `[]` if spaCy model not loaded)
   - enriched regex entities via `extract_enriched_entities` (`utils/intel_enrichment.py`)
   - external entities injected from the finding payload (ORG/EMAIL/USERNAME/PLATFORM)
   - merged via `_merge_entities`
4. **Classification** via `ModelManager.predict_primary/predict_secondary` (`utils/model_manager.py`)
5. **Semantic similarity** via `semantic_similarity` (sentence-transformers if available; TF-IDF fallback)
6. **Impact + priority**:
   - `estimate_impact` (`utils/intel_enrichment.py`)
   - `correlate_alerts` (`utils/intel_enrichment.py`)
   - `prioritize_alert` (`utils/intel_enrichment.py`)

### Organization relevance (“external intelligence verification fields”)

- **File**: `intelligence/relevance_engine/__init__.py`
- **Key symbols**:
  - `OrganizationProfile`, `RelevanceAssessment`
  - `resolve_organization_profile(query, watchlist=None)`
  - `assess_organization_relevance(...)`

Called from `ThreatIntelligenceEngine._apply_relevance_assessment` (`utils/nlp_engine.py`), which:

- Builds a **broad** `candidate_entities` list (entities + pattern buckets + external intel fields)
- Runs `assess_organization_relevance`
- Writes results into:
  - `result["relevance_assessment"]` (public dict)
  - `result["entities"]` (filtered)
  - `result["external_intelligence"][...]` fields like `verification_status`, `verified_org_match`, `relevance_score`, `suppressed_noise`, etc.

### Correlation + case creation gating

There are **two** “correlation” concepts:

1. **Campaign-style correlation embedded in the alert result** — `correlate_alerts` in `utils/intel_enrichment.py` (field `result["correlation"]`).
2. **Case creation eligibility** — `assess_correlation` in `intelligence/correlation/__init__.py` returning `CorrelationAssessment`.

`CorrelationAssessment.should_create_case` is the boolean gate for:

- `ThreatIntelligenceEngine.collect_external_intelligence` (when `persist=True`)
- `ThreatIntelligenceEngine.sync_watchlist` (always persists eligible cases)

### Case construction + verification badge + sensitive detection + scoring

All in `ThreatIntelligenceEngine._build_exposure_case` (`utils/nlp_engine.py`):

1. **Evidence packet** assembled (`evidence_payload`)
2. **Sensitive detector**:
   - `detect_sensitive_data(...)` (`intelligence/sensitive_detector/detector.py`)
   - Produces masked samples + `sensitive_data_types` + `sensitive_risk_score`
3. **Case scoring**:
   - `score_case(...)` (`intelligence/scoring/__init__.py`) → `CaseScore` used for priority/severity/confidence fields
4. **Verification badge**:
   - `compute_verification_status(case_payload)` (`intelligence/verification_engine/verifier.py`)
   - Writes `verification_badge`, `verification_score`, `verification_reasons`

### Case persistence + dedupe/merge

- **Write path**: `MongoManager.save_case` → `LocalMonitoringStore.save_case` (`utils/local_store.py`)
- **Normalization**: `normalize_case_record` / `normalize_case_list` (`utils/case_schema.py`)
- **Merge logic** (high-signal for debugging duplicates):
  - `_find_matching_case`, `_merge_evidence`, `_merge_sensitive_findings`, `_dedupe_strings`, `token_similarity` (`utils/local_store.py`)

## Sensitive data detector (backend)

- **File**: `intelligence/sensitive_detector/detector.py`
- **Functions**: `detect_sensitive_data`, `_detect_sensitive_data_cached` (LRU), `_mask_value`
- **Models**: `intelligence/sensitive_detector/models.py` (`SensitiveFinding`, `SensitiveDetectionResult`)
- **Patterns**: `intelligence/sensitive_detector/patterns.py`
- **Luhn helper**: `intelligence/sensitive_detector/luhn.py` (`passes_luhn`)

## Cyber cell reporting flow (backend)

- **File**: `services/cyber_cell_reporting/__init__.py`
- **Key symbols**:
  - `CyberCellReportRequest` (pydantic model)
  - `build_preview(db, payload, user_id=...)`
  - `send_report(db, payload, user_id=...)`
  - `get_reporting_status()` (delegates to email sender status)
  - `_resolve_cases`, `_generate_attachments`, `_ensure_send_rate_limit`

Email transport:

- **File**: `services/cyber_cell_reporting/email_sender.py`
- **Key symbols**:
  - `reporting_delivery_status`, `send_cyber_cell_email`, `validate_email_list`, `build_recipient_lists`
  - Exception: `CyberCellEmailError`

Auditing:

- **File**: `services/cyber_cell_reporting/audit_logger.py`
  - `record_preview_audit`, `record_send_audit`, `record_rate_limit_audit`

## CVRP signed report flow (backend)

### PDF export route

- **Route**: `GET /export/report/pdf`
- **Handler**: `export_pdf_report` (`backend/main.py`)
- **PDF generator**: `generate_pdf_report` (`utils/reporting.py`)
- **Signing + persistence**: `create_signed_report_record` (`services/signed_reports.py`)

### Public verification API routes

- **GET** `/api/v1/verify/report/{report_id}` → `get_report_verification_details`
  - Uses `verification_response_cache` (`services/report_verification_cache.py`)
  - Builds response via `build_public_verification_response` (`services/signed_reports.py`)
- **POST** `/api/v1/verify/report/{report_id}/upload` → `verify_uploaded_report`
  - Invalidates cache, reads upload bytes, calls `verify_uploaded_report_bytes` (`services/signed_reports.py`)

### Browser convenience redirect

- **GET** `/verify/{report_id}` → `redirect_public_verify_page`
  - Redirects to `build_verification_url` (`security/report_signing/signing.py`)

### Cryptographic primitives (where to debug signature failures)

- **Signing entry**: `sign_report_payload` (`security/report_signing/signing.py`)
- **Canonical payload bytes**: `build_signed_payload_bytes` (`security/report_signing/verification.py`)
- **Signature verify**: `verify_signature` (`security/report_signing/verification.py`)
- **Hashing**: `compute_sha256` (`security/report_signing/hashing.py`)

## Routers / endpoints (complete list) + examples

All routes are registered in `backend/main.py`.

Legend:

- **Q** = query params
- **B** = JSON body

### `POST /analyze`

- **Handler**: `analyze`
- **Request (`AnalyzeRequest`)**:

```json
{
  "text": "Selling corporate VPN access with admin@acme.com password=Winter2026!"
}
```

- **Response (`AnalyzeResponse`)**: threat metadata + patterns + entities + correlation/impact/priority blocks (see model fields in `backend/main.py`).

### `GET /alerts`

- **Handler**: `get_alerts`
- **Query**: `limit` (default 100)
- **Response**:

```json
{
  "count": 12,
  "alerts": [{ "...": "mongo or local alert documents" }],
  "warning": null
}
```

`warning` is sourced from `engine.db.warning` (`MongoManager.warning`).

### `GET /stats`

- **Handler**: `get_stats`
- **Implementation**: `ThreatIntelligenceEngine.get_stats` (`utils/nlp_engine.py`) + `MongoManager.get_stats` (`utils/db.py`)

### `GET /monitoring/stats`

- **Handler**: `get_monitoring_stats`
- **Implementation**: `MongoManager.get_monitoring_stats` → `LocalMonitoringStore.get_case_stats`

### `GET /health`

- **Handler**: `health_check`
- **Response** (strings):

```json
{
  "status": "ok",
  "scheduler": "running",
  "watchlists": "3"
}
```

Note: `scheduler` is a **literal** `"running"` in code; it does not introspect thread health.

### `GET /verify/{report_id}`

- **Handler**: `redirect_public_verify_page`
- **Behavior**: HTTP 307 to `build_verification_url(report_id)` (`security/report_signing/signing.py`)

### `POST /collect-intel`

- **Handler**: `collect_intelligence`
- **Request (`CollectIntelRequest`)**:

```json
{
  "query": "acme.com",
  "persist": true,
  "demo": false
}
```

- **Response** (high-level keys returned by `ThreatIntelligenceEngine.collect_external_intelligence`):

```json
{
  "organization": "acme.com",
  "platforms": ["GitHub", "IntelX"],
  "findings": [{ "...": "normalized result dicts" }],
  "summary": { "...": "aggregated collection summary" },
  "warnings": [],
  "generated_at": "2026-04-16T12:34:56+00:00",
  "demo_mode": false,
  "stored_findings": 4,
  "case_updates": [{ "action": "created", "case_id": "case_...", "title": "...", "priority": "HIGH" }],
  "count": 6
}
```

### Cases

#### `GET /cases`

- **Handler**: `list_cases`
- **Query**: `limit`, `status`, `priority`, `search`

#### `GET /cases/export`

- **Handler**: `export_cases`
- **Response**: `LocalMonitoringStore.export_snapshot()` JSON

#### `GET /cases/{case_id}`

- **Handler**: `get_case`

#### `PATCH /cases/{case_id}`

- **Handler**: `update_case`
- **Request (`CaseUpdateRequest`)** example:

```json
{
  "case_status": "investigating",
  "owner": "IR Team",
  "business_unit": "Security Operations",
  "comment": "Escalated after verified domain exposure."
}
```

### Watchlists

#### `GET /watchlists` — `list_watchlists`

#### `POST /watchlists` — `create_watchlist`

Body uses `WatchlistRequest` (`backend/main.py`) and is normalized via `MonitoringScheduler.normalize_watchlist_payload`.

Example:

```json
{
  "name": "ACME Credential Monitoring",
  "query": "acme.com",
  "enabled": true,
  "interval_seconds": 300,
  "owner": "SOC",
  "business_unit": "Security Operations",
  "description": "Track public leak mentions for ACME",
  "webhook_url": "",
  "demo_mode": false,
  "tags": ["credentials"],
  "assets": ["vpn.acme.com", "sso.acme.com"]
}
```

#### `PUT /watchlists/{watchlist_id}` — `update_watchlist`

#### `DELETE /watchlists/{watchlist_id}` — `delete_watchlist`

#### `POST /watchlists/{watchlist_id}/run` — `run_watchlist_now`

### `GET /audit-events`

- **Handler**: `get_audit_events`
- **Query**: `limit` (default 100)

### `GET /events/stream`

- **Handler**: `stream_events` (SSE)

### `GET /export/report/pdf`

- **Handler**: `export_pdf_report`
- **Query**:
  - `start_date`, `end_date` (ISO strings; parsed in `utils/reporting.filter_cases`)
  - `severity` (repeatable query list)
  - `category` (repeatable query list)
  - `org_id` (optional)

Response: `FileResponse` PDF + CVRP headers (`X-Citadel-*`).

### Cyber cell routes

#### `POST /api/v1/report/cybercell/preview`

- **Handler**: `preview_cyber_cell_report`
- **Body**: `CyberCellReportRequest` (`services/cyber_cell_reporting/__init__.py`)

Minimal example:

```json
{
  "case_ids": ["case_abcd12345678"],
  "recipients": ["cybercell@example.gov.in"],
  "cc": [],
  "contact_person_details": {
    "name": "Jane Doe",
    "designation": "CISO",
    "email": "jane.doe@acme.com",
    "phone": "+1-555-0100"
  },
  "confirmation_flag": false
}
```

#### `GET /api/v1/report/cybercell/status`

- **Handler**: `cyber_cell_reporting_status`

#### `POST /api/v1/report/cybercell/send`

- **Handler**: `send_cyber_cell_report`
- **Requires**:
  - `confirmation_flag: true`
  - valid `preview_id` matching the preview fingerprint (`PreviewStore.validate`)

### Verification routes

#### `GET /api/v1/verify/report/{report_id}`

- **Handler**: `get_report_verification_details`

#### `POST /api/v1/verify/report/{report_id}/upload`

- **Handler**: `verify_uploaded_report`
- **Multipart**: field name `file` (`UploadFile = File(...)`)

## Storage schema notes (cases / alerts / audit / signed reports)

### Cases (`LocalMonitoringStore.save_case`)

Cases are **dicts** constrained/normalized by `ExposureCase` in `utils/case_schema.py`.

High-signal merge keys in `LocalMonitoringStore._find_matching_case` (`utils/local_store.py`):

- `event_signature`, `fingerprint_key`
- org/threat similarity
- overlapping assets/indicators/locations
- time window proximity
- snippet similarity (`token_similarity`)

### Alerts / analyses (`MongoManager.insert_analysis`)

External intel “alerts” are built in `ThreatIntelligenceEngine._persist_result_alert` (`utils/nlp_engine.py`) and stored either:

- in Mongo (`insert_one`) when connected, else
- appended to local `alerts` via `LocalMonitoringStore.insert_alert` (cap **2000**)

### Audit events (`LocalMonitoringStore.record_audit_event`)

Common `event_type` values emitted by the codebase include:

- `watchlist_created`, `watchlist_updated`, `watchlist_deleted` (`backend/main.py`)
- `watchlist_sync` (`ThreatIntelligenceEngine.sync_watchlist`)
- `webhook_delivery` (`MonitoringScheduler._dispatch_webhook_if_configured`)
- `cyber_cell_report_preview`, `cyber_cell_report_sent`, `cyber_cell_report_failed`, `cyber_cell_report_rate_limited` (`services/cyber_cell_reporting/audit_logger.py`)

### Signed reports (`LocalMonitoringStore.save_signed_report`)

Created by `create_signed_report_record` (`services/signed_reports.py`). Typical keys include:

- `report_id`, `org_id`, `created_by_user_id`, `created_at`, `report_type`
- `case_ids`, `pdf_file_path`, `pdf_sha256`, `evidence_sha256`
- `signature_base64`, `signing_algorithm`, `public_key_fingerprint`, `signature_status`, `signing_warning`
- `public_verification_url`, `expires_at`, `status`, `audit_reference_id`
- `signed_payload` (canonical signing input snapshot)
- `verification_summary` (aggregated metadata for public endpoint)

## Related docs

- Architecture: `docs/architecture_overview.md`
- Schemas: `docs/data_models.md`
- Pipeline trace: `docs/pipeline_trace.md`
- Debugging: `docs/debugging_playbook.md`
- Sequence diagrams: `docs/sequence_diagrams.md`
