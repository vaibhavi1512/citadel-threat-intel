# Pipeline Step-by-Step Trace (Pseudo Outputs)

This document simulates **one** external intelligence finding traveling through the system, from raw provider hit to **case JSON** to **SSE** to **PDF + signed report metadata**.

It references the real call chain:

`ExternalIntelligenceService.collect` → `ThreatIntelligenceEngine.collect_external_intelligence` / `sync_watchlist` → `_build_external_finding_result` → `_apply_relevance_assessment` → `assess_correlation` → `_build_exposure_case` → `MongoManager.save_case` → `LocalMonitoringStore.save_case`

Files:

- `utils/source_intel_service.py`
- `utils/nlp_engine.py`
- `intelligence/relevance_engine/__init__.py`
- `intelligence/correlation/__init__.py`
- `intelligence/scoring/__init__.py`
- `intelligence/sensitive_detector/detector.py`
- `intelligence/verification_engine/verifier.py`
- `utils/local_store.py`
- `utils/monitoring_runtime.py` (SSE)

## Scenario

- Watchlist query: **`acme.com`**
- Source: **LeakIX** (representative “LeakIX/GitHub/IntelX” family)
- Finding includes corporate emails + credential keywords + a URL location

---

## Step 0 — Raw provider hit (conceptual)

**Input event (provider-native)** — not stored verbatim as this object; collectors normalize into a `finding` dict consumed by `_build_external_finding_result`.

```json
{
  "source": "LeakIX",
  "organization": "acme.com",
  "text": "Leak: acme employee credentials | admin@acme.com user=jsmith passh=*** | breach dump | https://leak.example/threads/999",
  "emails": ["admin@acme.com"],
  "usernames": ["jsmith"],
  "data_types": ["credentials", "email addresses"],
  "source_locations": ["https://leak.example/threads/999"],
  "confidence_score": 82,
  "risk_score": 0.62,
  "event_signature": "leakix|acme.com|credential|9f2c...",
  "summary": "Credential-style leak mentioning acme.com with corporate email addresses."
}
```

---

## Step 1 — Collector aggregation + promotion (`ExternalIntelligenceService`)

**Transform**: multiple raw hits may aggregate; confidence is scored; low-signal items may be filtered upstream (`utils/signal_quality.py`).

**Pseudo output (`finding` passed into engine)**:

```json
{
  "source": "LeakIX",
  "organization": "acme.com",
  "type": "Credential Leak",
  "text": "...same text blob...",
  "emails": ["admin@acme.com"],
  "usernames": ["jsmith"],
  "data_types": ["credentials", "email addresses"],
  "data_breakdown": [{ "type": "credentials", "count": 1 }],
  "source_locations": ["https://leak.example/threads/999"],
  "confidence_score": 82,
  "risk_score": 0.62,
  "source_trust": 0.76,
  "confidence_reasons": ["High-confidence structured fields present"],
  "event_signature": "leakix|acme.com|credential|9f2c...",
  "volume": 1,
  "matched_indicators": ["admin@acme.com", "jsmith"],
  "affected_assets": ["admin@acme.com"],
  "raw_items": [{ "metadata": { "demo": false } }]
}
```

---

## Step 2 — Engine: normalize + enrich (`ThreatIntelligenceEngine._build_external_finding_result`)

**Transforms**:

- multilingual + slang + cleaning
- regex `patterns`
- entities (spaCy + enriched + external injected entities)
- semantic similarity + ML classifier output
- `correlate_alerts` + `prioritize_alert`

**Pseudo `result` (selected keys)**:

```json
{
  "source": "LeakIX",
  "threat_type": "Credential Leak",
  "risk_level": "MEDIUM",
  "risk_score": 0.62,
  "confidence_score": 0.84,
  "patterns": {
    "emails": ["admin@acme.com"],
    "passwords": [],
    "ips": [],
    "usernames": ["jsmith"],
    "platforms": ["LeakIX"]
  },
  "entities": [
    { "text": "acme.com", "label": "ORG" },
    { "text": "admin@acme.com", "label": "EMAIL" },
    { "text": "jsmith", "label": "USERNAME" }
  ],
  "correlation": { "correlated_alerts_count": 0, "campaign_score": 12 },
  "alert_priority": { "priority": "HIGH", "score": 78 },
  "external_intelligence": {
    "organization": "acme.com",
    "source": "LeakIX",
    "source_locations": ["https://leak.example/threads/999"],
    "data_types": ["credentials", "email addresses"],
    "event_signature": "leakix|acme.com|credential|9f2c..."
  }
}
```

---

## Step 3 — Relevance engine (`_apply_relevance_assessment` → `assess_organization_relevance`)

**Transforms**:

- expands candidate entities from patterns + external intel fields
- validates and filters entities against `OrganizationProfile` (`resolve_organization_profile`)

**Pseudo `result["relevance_assessment"]` (public dict)**:

```json
{
  "relevance_score": 78,
  "verified_org_match": true,
  "verification_status": "YES",
  "matched_indicators": ["admin@acme.com"],
  "matched_assets": { "emails": ["admin@acme.com"], "domains": ["acme.com"] },
  "matched_assets_flat": ["admin@acme.com", "acme.com"],
  "suppressed_noise": false,
  "suppression_reasons": [],
  "filtered_entities": [
    { "text": "admin@acme.com", "label": "EMAIL", "confidence": 0.92, "entity_type": "EMAIL" }
  ]
}
```

Also note: `_apply_relevance_assessment` copies several fields back into `result["external_intelligence"]` for downstream case building.

---

## Step 4 — Correlation gate for case creation (`assess_correlation`)

**Transform**: computes `CorrelationAssessment` using relevance output + text keywords + trust + validated entity counts.

**Pseudo `correlation_assessment`**:

```json
{
  "should_create_case": true,
  "correlation_score": 74,
  "relevance_score": 78,
  "validated_entity_count": 2,
  "matched_watchlist_entities": ["admin@acme.com", "acme.com"],
  "source_trust": 0.76,
  "suppressed_noise": false,
  "reasoning": [
    "Strong watchlist match on admin@acme.com, acme.com.",
    "Validated 2 entity signal(s) before case creation."
  ]
}
```

If `should_create_case` is false, `collect_external_intelligence` may still persist an “alert” (`_persist_result_alert`) but will **not** call `save_case` for that finding.

---

## Step 5 — Case construction (`_build_exposure_case`)

**Transforms**:

- chooses primary URL/channel (`choose_primary_location` in `utils/signal_quality.py`)
- builds `evidence[]` and `sources[]`
- runs `detect_sensitive_data` on evidence text
- runs `score_case` → sets priority/severity/confidence fields
- runs `compute_verification_status` → sets `verification_badge`

**Pseudo `case_payload` (selected keys)**:

```json
{
  "org_id": "acme.com",
  "organization": "acme.com",
  "title": "acme.com exposure detected via LeakIX",
  "category": "Credential Leak",
  "priority": "HIGH",
  "priority_score": 78,
  "severity": "High",
  "confidence_score": 84,
  "relevance_score": 78,
  "verified_org_match": true,
  "verification_status": "YES",
  "verification_badge": "LIKELY",
  "verification_score": 72,
  "sensitive_data_types": ["Credential Pair"],
  "sensitive_findings": [
    {
      "finding_type": "Credential Pair",
      "masked_value": "pa***rd",
      "source_evidence_id": "leakix::2026-01-10T00:00:00+00:00::leakix|acme.com|credential|9f2c...",
      "source_index": 0,
      "risk_weight": 12
    }
  ],
  "event_signature": "leakix|acme.com|credential|9f2c...",
  "fingerprint_key": "leakix|acme.com|credential|9f2c...",
  "evidence": [
    {
      "evidence_id": "leakix::2026-01-10T00:00:00+00:00::leakix|acme.com|credential|9f2c...",
      "evidence_type": "link",
      "source_platform": "LeakIX",
      "source_locations": ["https://leak.example/threads/999"],
      "matched_entities": ["admin@acme.com", "jsmith"]
    }
  ]
}
```

---

## Step 6 — Persistence + dedupe (`LocalMonitoringStore.save_case`)

**Transform**:

- `normalize_case_record` (`utils/case_schema.py`)
- merge if `_find_matching_case` matches on signature/fingerprint/assets/time/snippet similarity

**Pseudo storage outcome**:

```json
{ "action": "merged", "case_id": "case_01a2b3c4d5e6", "merged_fields": ["evidence", "last_seen", "priority_score"] }
```

or

```json
{ "action": "created", "case_id": "case_01a2b3c4d5e6" }
```

**Where stored**: `data/monitoring_state.json` under `cases[]` (path from `MONITORING_STATE_PATH` in `utils/config.py`).

---

## Step 7 — SSE to frontend (`MonitoringEventBus.publish`)

**Publisher** (example from watchlist path): `MonitoringScheduler._emit_case_events` (`utils/monitoring_runtime.py`)

**Pseudo SSE `data:` payload**:

```json
{
  "sent_at": "2026-04-16T12:34:56.000000+00:00",
  "event_type": "case_updated",
  "action": "created",
  "trigger": "scheduled",
  "watchlist_id": "watch_abcd",
  "watchlist_name": "ACME Credential Monitoring",
  "case": {
    "id": "case_01a2b3c4d5e6",
    "title": "acme.com exposure detected via LeakIX",
    "priority": "HIGH",
    "priority_score": 78,
    "case_status": "new",
    "last_seen": "2026-01-10T00:00:00+00:00"
  }
}
```

**Consumer**: `frontend-react/src/pages/Feed.jsx` parses JSON and calls `loadMonitoring()`.

---

## Step 8 — PDF report generation + signed record (`GET /export/report/pdf`)

**Server path**: `export_pdf_report` in `backend/main.py`

**Pseudo response headers**:

```http
X-Citadel-Report-Id: 6f3d...
X-Citadel-Verification-Url: http://127.0.0.1:5173/verify/6f3d...
X-Citadel-Signature-Status: signed
Content-Disposition: attachment; filename="citadel-exposure-report-20260416-123456.pdf"
```

**Pseudo `signed_reports[]` entry** (subset):

```json
{
  "report_id": "6f3d...",
  "org_id": "acme.com",
  "report_type": "executive",
  "case_ids": ["case_01a2b3c4d5e6"],
  "pdf_sha256": "sha256:...",
  "signature_status": "signed",
  "public_verification_url": "http://127.0.0.1:5173/verify/6f3d..."
}
```

## Related docs

- Architecture: `docs/architecture_overview.md`
- Backend details: `docs/backend_deep_dive.md`
- Diagrams: `docs/sequence_diagrams.md`
