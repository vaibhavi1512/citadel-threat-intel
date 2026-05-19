# Text-based Sequence Diagrams

These diagrams are **text-first** (no Mermaid requirement). They map to the actual symbols/files in this repo.

## 1) Monitoring event ingestion (scheduled watchlist)

Actors:

- `MonitoringScheduler` (`utils/monitoring_runtime.py`)
- `ThreatIntelligenceEngine` (`utils/nlp_engine.py`)
- `ExternalIntelligenceService` (`utils/source_intel_service.py`)
- `MongoManager` / `LocalMonitoringStore` (`utils/db.py`, `utils/local_store.py`)
- `MonitoringEventBus` (`utils/monitoring_runtime.py`)
- React `Feed` (`frontend-react/src/pages/Feed.jsx`)

```
MonitoringScheduler._run_loop
  -> engine.db.list_watchlists(enabled_only=True)
  -> for each due watchlist:
       MonitoringScheduler.run_watchlist(watchlist, trigger="scheduled")
         -> engine.sync_watchlist(watchlist)            [utils/nlp_engine.py]
              -> collect_external_intelligence(... persist=False, demo=watchlist.demo_mode)
                   -> ExternalIntelligenceService.collect|build_demo_collection
                   -> per finding:
                        _build_external_finding_result
                        _apply_relevance_assessment
                        assess_correlation
                        if should_create_case:
                          db.save_case(_build_exposure_case(...))
         -> MonitoringScheduler._emit_case_events
              -> event_bus.publish({event_type:"case_updated", ...})
         -> engine.db.record_watchlist_run(...)

Browser Feed.jsx (EventSource /events/stream)
  -> onmessage JSON.parse(event.data)
  -> if event_type == case_updated: loadMonitoring()
       -> parallel GET /monitoring/stats /cases /watchlists /audit-events ...
```

## 2) Case creation (manual `/collect-intel` with persistence)

Actors:

- FastAPI route `collect_intelligence` (`backend/main.py`)
- `ThreatIntelligenceEngine.collect_external_intelligence` (`utils/nlp_engine.py`)
- `MonitoringEventBus.publish` (`utils/monitoring_runtime.py`)

```
Client POST /collect-intel
  -> backend.main.collect_intelligence
       -> engine.collect_external_intelligence(query, persist=payload.persist, demo=payload.demo)
            -> per finding:
                 _persist_result_alert -> db.insert_analysis (Mongo OR local alerts fallback)
                 optional db.save_case(_build_exposure_case) if correlation gate passes
            -> returns { case_updates: [...] }
       -> for each case update:
            event_bus.publish({event_type:"case_updated", trigger:"manual_collect", ...})
```

## 3) PDF report generation + signed record + headers

Actors:

- `export_pdf_report` (`backend/main.py`)
- `generate_pdf_report` / `filter_cases` (`utils/reporting.py`)
- `create_signed_report_record` (`services/signed_reports.py`)
- `sign_report_payload` (`security/report_signing/signing.py`)
- `LocalMonitoringStore.save_signed_report` (`utils/local_store.py`)

```
Client GET /export/report/pdf?...filters...
  -> export_pdf_report
       -> db.list_cases(limit=5000)
       -> report_id = uuid
       -> verification_url = build_verification_url(report_id)
       -> filtered_cases = filter_cases(...)            [signing metadata scope]
       -> file_path = generate_pdf_report(cases, ..., verification_details=prepare_report_verification_details(...))
            -> internally filter_cases(...) again        [PDF scope]
       -> pdf_bytes = read(file_path)
       -> signed_report = create_signed_report_record(db, cases=filtered_cases, pdf_bytes=..., report_id=...)
            -> save_signed_report in monitoring_state.json
       -> return FileResponse + X-Citadel-* headers
```

## 4) Cyber cell reporting (preview → send) + audit + SSE

Actors:

- `preview_cyber_cell_report` / `send_cyber_cell_report` (`backend/main.py`)
- `build_preview` / `send_report` (`services/cyber_cell_reporting/__init__.py`)
- `send_cyber_cell_email` (`services/cyber_cell_reporting/email_sender.py`)
- `record_preview_audit` / `record_send_audit` (`services/cyber_cell_reporting/audit_logger.py`)
- `MonitoringEventBus.publish` (`utils/monitoring_runtime.py`)

```
Client POST /api/v1/report/cybercell/preview
  -> build_preview
       -> validate_case_selection
       -> generate_pdf_report + create_signed_report_record (+ optional JSON bundle bytes)
       -> record_preview_audit -> db.record_audit_event

Client POST /api/v1/report/cybercell/send
  -> send_report
       -> validate preview_id + fingerprint + confirmation_flag
       -> rate limit check via db.count_audit_events
       -> regenerate attachments + signed record (fresh PDF bytes)
       -> send_cyber_cell_email (SMTP or mock/disabled)
       -> record_send_audit
       -> maybe db.update_signed_report(status="sent")
  -> backend.main publishes:
       {event_type:"cyber_cell_report_sent"} OR {event_type:"cyber_cell_report_failed"}

Feed.jsx EventSource
  -> toast + optional reload on success path
```

## 5) CVRP verification flow (public metadata + upload verify)

Actors:

- `get_report_verification_details` / `verify_uploaded_report` (`backend/main.py`)
- `verification_response_cache` (`services/report_verification_cache.py`)
- `build_public_verification_response` / `verify_uploaded_report_bytes` (`services/signed_reports.py`)
- `verify_signature` (`security/report_signing/verification.py`)
- React `VerifyReport` (`frontend-react/src/pages/VerifyReport.jsx`)

```
VerifyReport.jsx useEffect
  -> GET /api/v1/verify/report/{reportId}
       -> verification_response_cache.get(report_id) OR
          db.get_signed_report(report_id) -> build_public_verification_response -> cache.set

User selects PDF file
  -> POST /api/v1/verify/report/{reportId}/upload (multipart field "file")
       -> verification_response_cache.invalidate(report_id)
       -> verify_uploaded_report_bytes(db, report_id, pdf_bytes)
            -> compare sha256(pdf_bytes) to stored pdf_sha256
            -> verify_signature(build_signed_payload_bytes(signed_payload), signature_base64, algorithm=...)
            -> return {verification_status, hash_match, signature_valid, ...}
```

## Related docs

- Architecture: `docs/architecture_overview.md`
- Backend: `docs/backend_deep_dive.md`
- Pipeline trace: `docs/pipeline_trace.md`
