# CITADEL Internal Documentation Index

This folder contains **read-only internal documentation** for the CITADEL Exposure Intelligence Platform repository (generated to match the code structure: FastAPI backend + JSON monitoring store + optional Mongo alerts + React/Vite frontend).

## Start here

1. `docs/architecture_overview.md` — components, storage split, main flows, SSE, exports
2. `docs/backend_deep_dive.md` — every FastAPI route, engine/pipeline symbols, persistence, signing/verification
3. `docs/frontend_deep_dive.md` — React pages/components, Axios client, SSE, PDF + cyber cell UI

## Deep dives

- `docs/data_models.md` — field-by-field schema notes (`ExposureCase`, evidence, org profiles, sensitive + verification, signed reports, audits)
- `docs/pipeline_trace.md` — step-by-step pseudo payloads from collector → case → SSE → PDF/signing
- `docs/debugging_playbook.md` — common failure modes + concrete files to instrument
- `docs/sequence_diagrams.md` — text-first sequence diagrams for the major flows

## Additional references (already in repo)

These existed before this documentation pass and remain useful design context:

- `docs/current_pipeline_audit.md`
- `docs/verification_and_sensitive_detection_design.md`
- `docs/cyber_cell_reporting_design.md`
- `docs/cvrp_signed_report_design.md`

## Shreyas checklist (read order)

If **Shreyas** reads these files in order, he should be able to run, explain, and debug the system end-to-end without getting stuck:

1. **`docs/architecture_overview.md`**
   - [ ] Can explain the **two persistence layers** (Mongo alerts vs `monitoring_state.json` domain state)
   - [ ] Can trace **watchlist scheduled monitoring** from scheduler thread → engine → case save → SSE → UI refresh
2. **`docs/backend_deep_dive.md`**
   - [ ] Can enumerate **all FastAPI routes** in `backend/main.py` and what each calls on `engine` / `engine.db` / `scheduler` / `event_bus`
   - [ ] Can explain **SSE framing** (`MonitoringEventBus.publish` + `GET /events/stream`)
   - [ ] Can explain **case gating** (`assess_correlation`) vs **alert correlation** (`correlate_alerts`)
3. **`docs/data_models.md`**
   - [ ] Can map a case JSON object to **`ExposureCase`** fields and know which fields are legacy mirrors
   - [ ] Can explain **signed report dict** fields produced by `create_signed_report_record`
4. **`docs/pipeline_trace.md`**
   - [ ] Can walk a single LeakIX-style finding through normalization, relevance, correlation, scoring, sensitive detection, verification badge, persistence
5. **`docs/debugging_playbook.md`**
   - [ ] Can diagnose “no cases created”, “SSE degraded”, “SMTP not sending”, “CVRP verify INVALID”, and “JSON store reset” scenarios
6. **`docs/frontend_deep_dive.md`**
   - [ ] Knows where **`EventSource`** lives (`Feed.jsx`) and how Axios base URL is resolved (`services/api.js`)
   - [ ] Can explain **PDF export** and **cyber cell modal** client gates and which backend endpoints they hit
7. **`docs/sequence_diagrams.md`**
   - [ ] Can whiteboard the system from diagrams without opening code

If anything above fails while reading, treat it as a signal to update these docs (separate change) to match the code truth.
