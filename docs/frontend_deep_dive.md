# Frontend Deep Dive — React (Vite) UI

Primary app: `frontend-react/`.

## Entry points + routing

### Bootstrap

- `frontend-react/src/main.jsx`
  - Wraps the app with `BrowserRouter`.

### Router + shell

- `frontend-react/src/App.jsx`
  - Global background + `Navbar`
  - Polls backend health every 10s via `getHealth()` (`frontend-react/src/services/api.js`)
  - Defines routes in `<Routes>`:
    - `/` → redirect to `/analyzer`
    - `/analyzer` → `Analyzer`
    - `/dashboard` → `Dashboard`
    - `/monitor` → `Feed` (monitoring workspace)
    - `/feed` → redirect to `/monitor`
    - `/upload` → `Upload`
    - `/verify/:reportId` → `VerifyReport`

## API layer (Axios)

- **File**: `frontend-react/src/services/api.js`
- **Axios instance**: `api` with `baseURL: resolveApiBaseUrl()` and `timeout: 15000` (some calls override to 60s)

### Base URL resolution (`resolveApiBaseUrl`)

Rules:

1. If `import.meta.env.VITE_API_BASE_URL` is set → use it directly.
2. Else in browser → `${window.location.protocol}//${window.location.hostname}:${VITE_API_PORT||8001}`
3. Else fallback → `http://127.0.0.1:${port}`

This function is also imported by `Feed.jsx` for SSE construction.

### Wrapper functions (representative)

Core monitoring:

- `getMonitoringStats` → `GET /monitoring/stats`
- `getCases` → `GET /cases`
- `getCase` → `GET /cases/{id}`
- `updateCase` → `PATCH /cases/{id}`
- `getWatchlists`, `createWatchlist`, `updateWatchlist`, `deleteWatchlist`, `runWatchlistNow`
- `getAuditEvents` → `GET /audit-events`
- `exportCasesSnapshot` → `GET /cases/export`

Analysis:

- `analyzeText` → `POST /analyze`
- `collectIntel` → `POST /collect-intel` (60s timeout)

PDF + cyber cell + verification:

- `exportPdfReport` → `GET /export/report/pdf` (blob; reads `content-disposition` + `x-citadel-*` headers)
- `previewCyberCellReport`, `sendCyberCellReport`, `getCyberCellReportingStatus`
- `getVerifiedReport`, `verifyReportUpload`

## State management (React)

There is **no Redux** / **no global store** in this package.

State is primarily:

- `useState` / `useEffect` / `useMemo` inside pages (`frontend-react/src/pages/*`)
- Local UI state inside components (`frontend-react/src/components/*`)

The closest “hook library” is an inline hook `useTypedText` defined inside `Analyzer.jsx` (not under `src/hooks/`).

## Pages (`frontend-react/src/pages/`)

### `Analyzer.jsx`

Purpose:

- Manual text analysis (`analyzeText`)
- Manual external collection (`collectIntel`)

Typical UI pieces:

- `TerminalConsole`, `ThreatCard`, `ExternalIntelOverview`, `Toast`, `Loader`

### `Dashboard.jsx`

Purpose:

- Executive charts based on `getMonitoringStats()`
- PDF export (`exportPdfReport`)
- Cyber cell modal entry point (`CyberCellReportModal`)

Notable UX constraints:

- PDF export requires selecting an `orgId` first (`handleExportPdf`)
- Cyber cell modal requires selecting an org (`handleOpenCyberCellReport`)

### `Feed.jsx` (monitoring workspace)

Purpose:

- Operator console for cases + watchlists + audit trail
- SSE live updates
- JSON export snapshot download (`exportCasesSnapshot`)
- Cyber cell modal from selected case context

Key state:

- `cases`, `watchlists`, `auditEvents`, `stats`
- `selectedCaseId`, `selectedCase`
- `filters` (status/priority/search)
- `liveState` (`connecting|live|degraded`)
- `reportingOpen`, `reportRequest` for cyber cell modal

Data refresh model:

- `loadMonitoring()` pulls parallel endpoints via `Promise.all`
- SSE triggers `loadMonitoring()` on `case_updated` and successful cyber cell sends

### `Upload.jsx`

Purpose:

- CSV-ish upload workflow; ultimately calls `analyzeText` for rows (see file for details)

### `VerifyReport.jsx`

Purpose:

- Public verification UI for `/verify/:reportId`
- Loads metadata via `getVerifiedReport`
- Upload PDF to `verifyReportUpload`

## Components (`frontend-react/src/components/`)

### `CyberCellReportModal.jsx`

Purpose:

- End-to-end cyber cell flow: **status → preview → send**
- Uses:
  - `getCyberCellReportingStatus`
  - `previewCyberCellReport`
  - `sendCyberCellReport`

Important client rules:

- Send button is disabled unless:
  - `confirmationFlag` checked
  - preview exists (`preview.preview_id`)
  - eligible cases exist and **no rejected cases** (strict gating mirrored from backend)
  - `status.live_delivery_ready` is true (UI-side guard; backend still enforces)

Payload construction:

- Built in `useMemo` as `payload` from `defaultRequest` + form fields.

### `CaseDetailPanel.jsx`

Purpose:

- Case editor + workflow updates via `onSave`
- Optional cyber cell launcher via `onReportToCyberCell` prop

### Other shared UI

- `Navbar.jsx`, `StatCard.jsx`, `RiskBadge.jsx`, `Loader.jsx`, `Toast.jsx`, `TerminalConsole.jsx`, `WatchlistManager.jsx`, `UploadBox.jsx`, etc.

## SSE: how updates reach the UI

- **Only** `frontend-react/src/pages/Feed.jsx` uses `EventSource`.
- URL: ``${resolveApiBaseUrl()}/events/stream``
- Parses `event.data` as JSON and switches on `payload.event_type`.

Backend must publish JSON strings (it does, via `MonitoringEventBus.publish`).

## PDF export: how it works in the browser

Flow in `Dashboard.jsx`:

1. Calls `exportPdfReport({ orgId, startDate, endDate, severity, category, onDownloadProgress })`
2. Receives `{ blob, filename, reportId, verificationUrl, signatureStatus }`
3. Creates an object URL and triggers a download (`<a download>`)

No client-side PDF rendering library is required; the PDF is produced server-side (`utils/reporting.py`).

## Cyber cell reporting UI (operator expectations)

Entry points:

- `Dashboard.jsx` opens modal with `defaultRequest` derived from export filters.
- `Feed.jsx` opens modal with a `reportRequest` tailored to a selected case (case id list, org id, etc.).

Backend requirements mirrored in UI:

- Preview must succeed and show eligible cases
- User must confirm checkbox (`confirmation_flag` analog in UI)
- Live SMTP readiness is surfaced via `/api/v1/report/cybercell/status`

## Related docs

- Backend routes: `docs/backend_deep_dive.md`
- SSE + scheduler publisher: `docs/architecture_overview.md`
- Sequences: `docs/sequence_diagrams.md`
