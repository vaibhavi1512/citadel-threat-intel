# Debugging Playbook — CITADEL (FastAPI + JSON Store + React)

This playbook is optimized for **fast root-cause isolation**. It intentionally references **exact files/symbols**.

## Golden rules (save hours)

1. **Two storage systems**:
   - **Mongo**: “alerts/analyses” via `MongoManager.insert_analysis` / `fetch_alerts` (`utils/db.py`)
   - **JSON**: “monitoring domain” (cases/watchlists/audit/signed reports) via `LocalMonitoringStore` (`utils/local_store.py`, path `MONITORING_STATE_PATH` in `utils/config.py`)
2. **If Mongo is disabled/unavailable**, `/alerts` may still “work” but come from **local fallback** alerts with warnings (`MongoManager.warning`).
3. **SSE payloads are JSON strings** produced by `MonitoringEventBus.publish` (`utils/monitoring_runtime.py`). If the UI shows `degraded`, it’s often JSON parse issues or stream disconnects (`frontend-react/src/pages/Feed.jsx`).
4. **Signed report verification caching** can hide fresh DB edits for up to `REPORT_VERIFICATION_CACHE_TTL_SECONDS` (`utils/config.py`, cache in `services/report_verification_cache.py`). Upload verification invalidates cache (`backend/main.py` `verify_uploaded_report`).

---

## Common failure points (symptoms → first checks)

### A) “Watchlists run but nothing changes”

Checklist:

1. **Is the watchlist enabled?** (`enabled` field) — scheduled loop only pulls enabled watchlists (`MonitoringScheduler._run_loop`).
2. **Is `next_run_at` in the future?** Updated on each run in `LocalMonitoringStore.record_watchlist_run`.
3. **Is demo mode accidentally on?** Watchlist `demo_mode` forces demo collection (`ThreatIntelligenceEngine.sync_watchlist` → `collect_external_intelligence(... demo=...)`).
4. **Are cases being filtered by correlation gate?** Inspect `finding["correlation_assessment"]` in API responses from `/collect-intel` (`ThreatIntelligenceEngine.collect_external_intelligence`).

### B) “`/collect-intel` returns findings but no cases”

This is commonly **`CorrelationAssessment.should_create_case == false`**.

Debug steps:

1. Inspect each finding’s:
   - `correlation_assessment` dict (`intelligence/correlation/__init__.py` `assess_correlation`)
   - `relevance_assessment` dict (`ThreatIntelligenceEngine._apply_relevance_assessment`)
2. Pay special attention to these gates in `assess_correlation`:
   - `suppressed_noise` must be false
   - `len(matched_assets) > 0` requires relevance output to include matched indicators/assets
   - `validated_entity_count > 0` requires entities labeled as `DOMAIN|EMAIL|IP|TOKEN|WALLET` **and** `confidence >= 0.7`
   - `relevance_score >= 45`
   - `correlation_score >= 55` (unless you changed defaults)

### C) “SSE works in Postman but UI doesn’t update”

Checklist:

1. Confirm the browser is actually connected to the same host/port as Axios (`resolveApiBaseUrl()` in `frontend-react/src/services/api.js`).
2. Confirm the UI parses JSON successfully (`Feed.jsx` marks `liveState` degraded on parse failure).
3. Remember the bus replays last messages on subscribe (`MonitoringEventBus.subscribe`), so you can see bursts on refresh.

### D) “`monitoring_state.json` reset / empty cases”

`LocalMonitoringStore._load` **replaces corrupt JSON** with defaults (`utils/local_store.py`). If the file is hand-edited and invalid JSON is introduced, the service can appear to “lose state”.

Mitigation for debugging:

- Back up `data/monitoring_state.json` before experiments.
- Prefer API mutations over manual JSON edits.

### E) “PDF export differs from what I see in `/cases`”

There are **two different filters** in `export_pdf_report` (`backend/main.py`):

- `filter_cases(...)` is used for **signing metadata** (`filtered_cases` passed into `create_signed_report_record`)
- `generate_pdf_report` is called with the **full** `cases` list and applies filtering internally via `filter_cases` again (`utils/reporting.generate_pdf_report`)

If you see mismatches around counts/metadata, compare:

- `engine.db.list_cases(limit=5000)` output vs `filter_cases(...)` output (`utils/reporting.py`)

---

## How to debug SSE

### Server-side

- **Publisher**: `MonitoringEventBus.publish` (`utils/monitoring_runtime.py`)
  - If you suspect events aren’t emitted, add temporary logging at publish sites:
    - `MonitoringScheduler._emit_case_events`
    - `MonitoringScheduler.run_watchlist` error path (`watchlist_error`)
    - `backend/main.py` `collect_intelligence` manual publish loop
    - `backend/main.py` `send_cyber_cell_report` success/failure publishes
- **Stream**: `stream_events` (`backend/main.py`)
  - Heartbeats every ~15s on idle (`queue.Empty`)

### Client-side

- **Only consumer** in React: `frontend-react/src/pages/Feed.jsx`
  - Watch `liveState`: `connecting | live | degraded`
  - If degraded: log `event.data` raw string (temporarily) when JSON parse fails

### Practical curl

SSE is easiest to observe with a browser, but you can also `curl -N` the stream and confirm `data: {...}` lines are arriving.

---

## How to debug pipeline noise (too many cases / too few)

### “Too many”

Focus on:

- **Relevance suppression**: `relevance_assessment.suppressed_noise` (`intelligence/relevance_engine`)
- **Correlation gate**: `should_create_case` (`intelligence/correlation`)
- **PDF/report noise**: `suppressed_noise` cases are filtered out of PDFs (`utils/reporting.filter_cases`)

### “Too few”

Focus on:

- **Validated entity confidence**: correlation requires `confidence >= 0.7` on validated entity types (`assess_correlation`)
- **Matched assets empty**: if relevance didn’t populate `matched_indicators`, correlation may fail `len(matched_assets) > 0`
- **Source trust**: low trust can block case creation unless evidence clarity is “strong” (`evidence_is_strong` logic)

### Collector-level debugging

- `utils/source_intel_service.py` (`ExternalIntelligenceService`)
- Noise tooling: `utils/signal_quality.py` (`should_promote_finding`, `is_likely_noise`, etc.)
- Optional env: `DEBUG_REJECTED_NOISE` (`utils/config.py`) — used by some noise-debug paths (grep before relying on it)

---

## How to debug wrong entity extraction

### External intel path

Primary entity merge happens in `ThreatIntelligenceEngine._build_external_finding_result` (`utils/nlp_engine.py`):

- spaCy entities (`extract_entities`)
- enriched regex entities (`extract_enriched_entities`)
- injected “external_entities” from the finding payload (ORG/EMAIL/USERNAME/PLATFORM)

Then relevance filters them in `_apply_relevance_assessment`.

### Text-only analyzer path

Use `/analyze` and inspect:

- `entities` vs `enriched_entities`
- whether spaCy model is missing (`extract_entities` returns `[]` if `_load_spacy()` fails)

---

## How to debug email sending (cyber cell)

### Status endpoint (fastest)

- `GET /api/v1/report/cybercell/status` → `reporting_delivery_status()` (`services/cyber_cell_reporting/email_sender.py`)

It returns:

- `enabled`, `mock_mode`, `mode`, `smtp_ready`, `live_delivery_ready`, `reasons`

### Common config issues

From `utils/config.py`:

- **`REPORTING_ENABLED` must be true** or `send_cyber_cell_email` raises `CyberCellEmailError`
- **`REPORTING_MOCK_MODE` true** short-circuits SMTP (still returns success objects)
- **SMTP variables** must be coherent:
  - `SMTP_HOST` required
  - sender: `SMTP_FROM_EMAIL` or `SMTP_USER`

### Rate limiting

Cyber cell sends are capped per org per day:

- `CYBER_CELL_DAILY_SEND_LIMIT` (`utils/config.py`)
- Enforced in `_ensure_send_rate_limit` (`services/cyber_cell_reporting/__init__.py`) via `MongoManager.count_audit_events`

### Auditing for forensic debugging

Search `audit_events` for:

- `cyber_cell_report_preview`
- `cyber_cell_report_sent` / `cyber_cell_report_failed`
- `cyber_cell_report_rate_limited`

Emitted by `services/cyber_cell_reporting/audit_logger.py`.

---

## How to debug CVRP signature verification

### Symptom: `signature_status` is `unsigned` but signing is enabled

Trace:

1. `get_signing_runtime_status` (`security/report_signing/signing.py`)
2. `sign_report_payload` warnings → stored into signed report `signing_warning` (`services/signed_reports.py`)

### Symptom: upload verification always INVALID

Trace `verify_uploaded_report_bytes` (`services/signed_reports.py`):

- `hash_match`: compares `compute_sha256(pdf_bytes)` to stored `pdf_sha256`
- `signature_valid`: verifies over **`signed_payload` dict** canonicalized by `build_signed_payload_bytes` (`security/report_signing/verification.py`)

Important detail:

- If the PDF bytes change after signing (even whitespace changes in generation), `hash_match` fails.

### Symptom: GET verification “stale”

- Check `verification_response_cache` TTL: `REPORT_VERIFICATION_CACHE_TTL_SECONDS` (`utils/config.py`)
- Upload route invalidates: `verification_response_cache.invalidate(report_id)` (`backend/main.py`)

---

## Log files / useful print statements

### Log files

This repo does **not** ship a dedicated rotating log file path in code reviewed here; operational logs are primarily:

- **Uvicorn stdout/stderr** (when you run `python backend/main.py` or `uvicorn ...`)
- **Python logging** for some modules (example: `logger = logging.getLogger(__name__)` in `utils/nlp_engine.py`)

### High-signal breakpoints (where to insert temporary logs)

- **Watchlist scheduling**: `MonitoringScheduler._run_loop`, `run_watchlist` (`utils/monitoring_runtime.py`)
- **Case gating**: `assess_correlation` (`intelligence/correlation/__init__.py`)
- **Relevance**: `assess_organization_relevance` (`intelligence/relevance_engine/__init__.py`)
- **Case creation**: `_build_exposure_case` (`utils/nlp_engine.py`)
- **Persistence merge**: `LocalMonitoringStore.save_case`, `_find_matching_case` (`utils/local_store.py`)
- **PDF**: `generate_pdf_report`, `filter_cases` (`utils/reporting.py`)
- **Signing**: `create_signed_report_record` (`services/signed_reports.py`)

---

## Related docs

- Backend reference: `docs/backend_deep_dive.md`
- Pipeline trace: `docs/pipeline_trace.md`
- Frontend behavior: `docs/frontend_deep_dive.md`
