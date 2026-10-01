# v1.0.0-rc1 release evidence

This is a live evidence record. Pending items are explicit; documentation of a planned gate is not a passing result.

| Evidence | Result |
|---|---|
| Repository | `rrahul0904/syntheticforge-ai` |
| Implementation branch / tested source commit | `codex/syntheticforge-v1-production` / `30ef1486cd144239ad7434f395d83a37e22505eb` |
| Candidate version | `1.0.0-rc1` |
| Verification date | 2026-10-01 |
| Baseline test count | 61 passed before implementation changes |
| Current test result | `make verify`: **68 passed, 2 skipped, 1 warning**; skips are PostgreSQL/MySQL service tests without local service DSNs |
| Python versions | Local Python 3.13.0 passed. CI for Python 3.11 and 3.12 is configured and has not run for this branch |
| SQLite source-to-target | Passed: 2 tables, 50 generated rows, validation 100.0, target counts 25/25, zero broken FKs, source unchanged |
| Browser E2E | Passed local HTTPS Playwright/Chromium: production login/logout, dashboard, DDL generation+validation, ZIP download, SQLite live test/introspection, project creation, JSON Schema/OpenAPI/Avro/CSV parse, agent run+artifact, app restart with project/session persistence, mobile viewport, no page errors |
| Production image | Base digest pinned, non-root and HTTP smoke job configured; image was not built locally under the no-container instruction, and hosted CI has not run |
| PostgreSQL / MySQL | Source-to-target, read-only, bad-credential, network, and missing-table service tests are configured; not executed locally because no databases/containers were started, and hosted CI has not run |
| SQL Server / Oracle / Snowflake / BigQuery / Redshift | Contract / code review only; no live certification claimed |
| AI provider | Not live-tested; deterministic local mode remains available |
| Parquet | Passed runtime generation/reload and streaming reload: 2 rows, scalar types, null, list and struct values verified |
| Million-row benchmark | Not rerun. The previous 170.144-second run reported invalid macOS RSS units; script is corrected and remains pending to avoid extra local load |
| Restart persistence | Passed local production-mode Uvicorn stop/restart on the same `/data` directory; the browser verified project data and session survived |
| Production security | Passed fail-closed config, auth/CSRF/roles/logout, source connector policy, receipt redaction, exact write approval, expiry/replay, and interactive row-limit tests |
| Deployment URL | None. `DEPLOYMENT_READY_BUT_NOT_EXECUTED` |
| Tracker workbook | Not modified; proposal recorded in `PROJECT_TRACKER_UPDATE.md` |
| Hosted CI | Workflow configured; no run result because GitHub CLI is absent and push credentials are unavailable |

## Explicit non-claims

This candidate does not claim external deployment, universal connector certification, external AI certification, horizontal scalability, or HIPAA/GDPR/SOC 2 certification. The authoritative state backend remains single-node SQLite. Browser checks passed locally; container build/smoke and hosted CI remain pending.
