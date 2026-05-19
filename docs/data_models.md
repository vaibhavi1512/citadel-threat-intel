# Data Model Documentation — Cases, Evidence, Org Profiles, Signed Reports, Sensitive + Verification

This document explains the **schemas as defined/consumed in code**. Primary sources:

- `utils/case_schema.py` (Pydantic models + `normalize_case_record`)
- `intelligence/relevance_engine/__init__.py` (`OrganizationProfile`, `RelevanceAssessment`)
- `services/signed_reports.py` (signed report dict shape + verification response dict)
- `intelligence/sensitive_detector/models.py` (`SensitiveFinding`, `SensitiveDetectionResult`)
- `intelligence/verification_engine/models.py` (`VerificationResult`)

## `ExposureCase` (`utils/case_schema.py`)

`ExposureCase` is the **canonical normalized case record**. At rest, cases are dicts inside `data/monitoring_state.json`, but they are normalized through `normalize_case_record` (`utils/case_schema.py`) on read/write paths in `utils/local_store.py`.

### Identity + timestamps

- **`case_id`**: canonical case identifier string.
- **`id`**: duplicate identifier for legacy compatibility; normalized to match `case_id`.
- **`created_at`**: ISO timestamp; normalized from `created_at` / `first_seen` / `last_seen` depending on what exists.
- **`updated_at`**: ISO timestamp; normalized from `updated_at` / `last_seen`.
- **`first_seen`**: earliest known observation time for the case.
- **`last_seen`**: latest observation time for the case.

### Organization context

- **`org_id`**: string key used for grouping and filtering (PDF export uses `org_id` query param).
- **`organization`**: human-readable org label.
- **`query`**: originating watchlist/query string when applicable.

### Taxonomy: category + severity + risk/priority legacy fields

- **`category`**: normalized enum-like label (`CaseCategory` literals in `utils/case_schema.py`, e.g. `"Credential Leak"`, `"Database Dump"`, `"Unknown"`).
- **`severity`**: `SeverityLevel` (`Critical|High|Medium|Low`) derived from `priority` / `priority_score` if needed (`_priority_to_severity`).
- **`severity_score`**: numeric severity score (0–100).
- **`confidence_score`**: integer 0–100 (normalized from float inputs when needed).
- **`risk_score`**: integer 0–100 (normalized from float inputs when needed).

Legacy parallel fields (still present because dashboards and merges use them):

- **`priority`**: string like `CRITICAL|HIGH|MEDIUM|LOW` (driven heavily by scoring output).
- **`priority_score`**: int score used for sorting and severity mapping.
- **`risk_level`**: string risk label (`HIGH|MEDIUM|LOW`) used in older UI paths.
- **`threat_type`**: classifier threat label (often aligned with `category`, but not always).

### Assets + indicators

- **`affected_assets`**: structured buckets (`AffectedAssets`: domains/emails/ips/usernames/tokens/wallets).
- **`affected_assets_flat`**: flattened unique string list used for search + merge (`flatten_affected_assets`, `_dedupe_strings`).
- **`matched_indicators`**: string indicators that matched during relevance/correlation (may overlap with assets).

### Evidence + sources

- **`evidence`**: list of `EvidenceItem` (normalized by `normalize_evidence_list`).
- **`sources`**: list of `SourceRecord` (normalized by `normalize_source_records`).
- **`extracted_entities`**: simplified extracted strings (may be derived from `matched_indicators` when legacy data exists).

#### `EvidenceItem` fields (`utils/case_schema.py`)

- **`evidence_id`**: stable-ish id; generated if missing (`"{source}-evidence-{index}"` pattern in `normalize_evidence_list`).
- **`evidence_type`**: `"screenshot" | "text" | "link" | "dump snippet"` (auto-inferred as `link` if any `source_locations` starts with `http`).
- **`source_platform`**: platform/source label for the evidence item.
- **`raw_snippet`**: raw text excerpt from the source.
- **`cleaned_snippet`**: analyst-friendly excerpt (fallbacks to summary/raw).
- **`matched_entities`**: strings tied to the evidence item (may include indicators).
- **`timestamp`**: ISO timestamp for the evidence observation.
- **`source_locations`**: URLs/channels/locations where the evidence was observed.
- **`provenance`**: dict metadata (often includes query + raw_items samples for external intel).
- **`legacy_summary`**: optional legacy `summary` string preserved for backwards compatibility.
- **`data_breakdown`**: optional structured leak metadata list (from external intel collectors).

#### `SourceRecord` fields (`utils/case_schema.py`)

- **`source`**: source name (e.g., `LeakIX`, `GitHub`, `IntelX`).
- **`first_seen` / `last_seen`**: optional ISO timestamps for that source’s involvement.
- **`evidence_count`**: count hint from the collector/engine merge logic.
- **`source_locations`**: locations associated with that source.
- **`risk_score` / `confidence_score` / `trust_score`**: floats summarizing risk/confidence/trust for that source row.
- **`related_sources`**: optional list of related dicts (corroboration graph inputs).

### Narrative fields (human consumption)

- **`exposure_summary` / `technical_summary` / `executive_summary`**: layered summaries; normalization copies between them when some are missing.
- **`recommended_actions` / `suggested_remediation_steps`**: remediation guidance lists (often mirrored).
- **`why_flagged` / `why_this_was_flagged`**: “why alert exists” strings; normalized to keep them populated.
- **`correlation_reason`**: reasons from correlation assessment dict.
- **`confidence_reasoning` / `severity_reasoning`**: lists explaining scoring outputs.

### Organization relevance fields (case-level mirrors)

These mirror the relevance engine output (`RelevanceAssessment.to_public_dict`) after case construction:

- **`relevance_score`**: int 0–100 style score (as produced by relevance engine).
- **`relevance_reasons`**: list of strings explaining relevance decisions.
- **`verified_org_match`**: bool: whether evidence ties to org-owned assets strongly enough.
- **`verification_status`**: string like `YES/NO` (relevance-side “org match status”, not the badge).

### Verification badge fields (case-level “CITADEL verification”)

Populated by `compute_verification_status` (`intelligence/verification_engine/verifier.py`) at the end of `ThreatIntelligenceEngine._build_exposure_case`:

- **`verification_badge`**: `VERIFIED | LIKELY | WEAK_SIGNAL` (from `intelligence/verification_engine/rules.py` constants).
- **`verification_score`**: int score used for UI prioritization.
- **`verification_reasons`**: list of human-readable reasons for the badge.

### Suppression / noise controls

- **`suppressed_noise`**: bool; if true, correlation and verification treat the case as low-trust/noisy.
- **`suppression_reasons`**: list of reasons from relevance engine suppression logic.

### Confidence assessment object

- **`confidence_assessment`**: `ConfidenceAssessment`
  - **`score`**: int score (case builder sets it to `CaseScore.confidence_score`)
  - **`reasons`**: list of strings

### Workflow / ownership

- **`triage_status`**: `TriageStatus` (`New|Under Review|Verified|False Positive|Closed`) mapped from legacy `case_status` when needed.
- **`assigned_to` / `owner`**: ownership fields (merge logic prefers existing owner in some branches).
- **`business_unit`**: routing metadata (watchlist-provided or inferred in engine).
- **`tags`**: list of short strings (threat tags, sources, data types).
- **`watchlists`**: watchlist names associated with the case.

### Sensitive findings (case-level)

- **`exposed_data_types`**: broad “data exposure categories” from external intel (`external_intelligence.data_types` pathway).
- **`sensitive_data_types`**: detector output categories (strings like `Credit Card`, `AWS Secret Key`, etc.).
- **`sensitive_findings`**: list of `SensitiveFindingRecord` objects (masked).
- **`sensitive_risk_score`**: additional risk points from sensitive detection (bounded in detector).

### Volume estimates

- **`estimated_total_records`**: optional numeric estimate when known.
- **`estimated_total_records_label`**: human-readable estimate label.

### Correlation + dedupe keys

- **`event_signature` / `fingerprint_key`**: stable identifiers used for case merge (`LocalMonitoringStore._find_matching_case`).
- **`occurrence_count`**: how many times the case was seen (incremented on merges).
- **`source_count` / `evidence_count` / `corroborating_source_count`**: counts maintained by `LocalMonitoringStore.save_case`.

### Legacy workflow fields

- **`title` / `summary`**: legacy display fields; `title` is generated if missing.
- **`case_status`**: legacy string status (`new`, `investigating`, etc.) mirrored with `triage_status` mapping functions.

---

## `EvidenceItem` (see above)

This is the evidence unit stored under `ExposureCase.evidence`.

---

## `OrganizationProfile` (`intelligence/relevance_engine/__init__.py`)

Represents “what we believe the organization owns / looks like” for relevance scoring.

- **`org_name`**: canonical org label for the profile.
- **`org_keywords`**: keywords used for semantic/context matching in evidence.
- **`official_domains`**: owned domains used for strict domain/email matching.
- **`email_patterns`**: optional patterns for corporate email identification logic.
- **`known_ips`**: infrastructure IPs considered org-owned.
- **`known_brands`**: brand strings that imply org association in evidence.
- **`trusted_assets`**: allow-listed domains/assets for special-case acceptance.

**Storage location**: optional JSON file at `utils/config.py` → `ORG_PROFILES_PATH` (`data/organization_profiles.json`).

**Loader/merger functions**:

- `load_organization_profiles`, `save_organization_profiles`
- `resolve_organization_profile(query, watchlist=None)` merges stored profile + inferred profile.

---

## `RelevanceAssessment` (`intelligence/relevance_engine/__init__.py`)

This is the **output** of `assess_organization_relevance` (not persisted wholesale as a nested document on the case, but flattened into case fields and `result["relevance_assessment"]`).

Key fields:

- **`profile`**: the `OrganizationProfile` used for the assessment.
- **`filtered_entities`**: entities kept after validation/relevance checks.
- **`matched_assets`**: bucketed matched assets (`domains`, `emails`, etc.).
- **`matched_indicators`**: indicators considered strong org matches.
- **`relevance_score`**: int score used downstream in correlation + verification.
- **`relevance_reasons`**: explanation strings.
- **`verified_org_match`**: bool.
- **`verification_status`**: string (`YES/NO` style).
- **`suppressed_noise` / `suppression_reasons`**: noise suppression output.
- **`rejected_entities`**: rejected entity records for debugging false positives.

Helper:

- **`to_public_dict()`**: adds `matched_assets_flat` and `verified_asset_count`.

---

## Signed report record (CVRP / “SignedReportRecord”)

There is **no** separate Pydantic model named `SignedReportRecord` in code; the persisted object is a **`dict`** created by `create_signed_report_record` (`services/signed_reports.py`) and stored in `LocalMonitoringStore.save_signed_report` (`utils/local_store.py`) under `signed_reports[]`.

### Core identifiers

- **`report_id`**: UUID string for verification URLs and lookups.
- **`org_id`**: org scope for the report (may be `"multiple-organizations"` for executive exports).
- **`created_by_user_id`**: attribution string (`X-User-Id` header if present, else `"anonymous"` in PDF export route).
- **`created_at`**: ISO timestamp.
- **`report_type`**: `"executive"` for dashboard PDF exports, `"cybercell"` for cyber cell flow (`services/cyber_cell_reporting/__init__.py`).

### Case linkage + file artifacts

- **`case_ids`**: list of case IDs included in signing metadata (derived from `cases` argument).
- **`pdf_file_path`**: filesystem path to generated PDF under OS temp directory (`utils/reporting.generate_pdf_report`).
- **`pdf_sha256`**: SHA-256 of the PDF bytes (`security/report_signing.hashing.compute_sha256`).

### Optional evidence bundle hash (cyber cell)

- **`evidence_sha256`**: SHA-256 over optional JSON bundle bytes when `include_json_bundle` is enabled in cyber cell generation.

### Signature fields

- **`signature_base64`**: base64 signature bytes (or `null` if unsigned).
- **`signing_algorithm`**: e.g. `Ed25519` or `RSA-SHA256` depending on key material (`security/report_signing/signing.py`).
- **`public_key_fingerprint`**: fingerprint string of public key material used for verification.
- **`signature_status`**: `"signed"` / `"unsigned"` (and may become `"expired"` as a *record status* via local store expiry updates).
- **`signing_warning`**: human-readable error/disabled-reason string when signing fails or is disabled.

### Verification + expiry

- **`public_verification_url`**: URL pointing to frontend route `/verify/{report_id}` (built by `build_verification_url`).
- **`expires_at`**: ISO expiry timestamp (`REPORT_SIGNED_REPORT_EXPIRY_DAYS` in `utils/config.py`).

### Operational metadata

- **`status`**: lifecycle (`generated`, updated to `sent` in cyber cell success path via `MongoManager.update_signed_report`).
- **`audit_reference_id`**: links to audit event id when updated on successful send.

### Embedded summaries

- **`verification_summary`**: dict produced by `_build_verification_summary` (`services/signed_reports.py`) including severity/category counts and evidence counts.
- **`signed_payload`**: dict used as the signing input snapshot (`build_signed_report_payload`); verification recomputes bytes via `build_signed_payload_bytes`.

---

## `SensitiveDetectionResult` + `SensitiveFinding` (`intelligence/sensitive_detector/models.py`)

### `SensitiveFinding`

- **`finding_type`**: detector classification label (e.g., `Credit Card`, `Google API Key`).
- **`masked_value`**: masked representation safe for UI/logs (`detector._mask_value`).
- **`source_evidence_id` / `source_index`**: optional linkage back to evidence (engine sets these when converting detections into case `sensitive_findings`).
- **`risk_weight`**: per-finding weight used to accumulate risk.

### `SensitiveDetectionResult`

- **`sensitive_types`**: list of unique finding types detected.
- **`matched_samples`**: list of `SensitiveFinding`.
- **`risk_score_addition`**: capped additive risk points from sensitive matches.
- **`detection_reasons`**: list of reasons strings.

---

## `VerificationResult` (`intelligence/verification_engine/models.py`)

This is the **structured output** of `compute_verification_status` (`intelligence/verification_engine/verifier.py`):

- **`verification_badge`**: `VERIFIED` / `LIKELY` / `WEAK_SIGNAL`
- **`verification_score`**: int score (0–100)
- **`verification_reasons`**: list of strings explaining the decision

---

## Audit log schema (practical JSON shape)

Audit events are **dicts** appended by `LocalMonitoringStore.record_audit_event` (`utils/local_store.py`). Common keys:

- **`id`**: generated `audit_<uuid>` unless provided
- **`timestamp`**: ISO timestamp (always set)
- **`event_type`**: discriminator string (`watchlist_sync`, `cyber_cell_report_sent`, etc.)
- **Domain-specific fields**:
  - Cyber cell audits include recipients, hashes, signing metadata (`services/cyber_cell_reporting/audit_logger.py`)
  - Webhook deliveries include `target`, `result`, `error` (`MonitoringScheduler._dispatch_webhook_if_configured`)

`MongoManager.count_audit_events` filters on:

- `event_type`, `org_id`, `status`, `since` (compares against `send_timestamp` or `timestamp`) (`utils/db.py` → `utils/local_store.py`)

## Related docs

- Backend endpoints + flow: `docs/backend_deep_dive.md`
- Pipeline pseudo trace: `docs/pipeline_trace.md`
- Frontend mapping: `docs/frontend_deep_dive.md`
