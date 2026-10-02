# v1.0.0-rc1 release evidence

This record distinguishes evidence on the integrated local branch from hosted and external checks still required. It must be refreshed against the final PR head before merge.

| Evidence | Result |
|---|---|
| Repository / PR | `rrahul0904/syntheticforge-ai`, PR #2, base `main` |
| Candidate | `1.0.0-rc1` |
| Mission start head | `5e55c83243e742938776673abee96816ab1e75c6` |
| Integrated implementation SHA | `ab7c6644b692969217c2b4fcab2ed8fec008c3a3` (local code; final evidence docs and benchmark results follow) |
| Local verification date | 2026-10-02 |
| PR state | Open; latest integrated code is not yet pushed as the PR head; not merged |
| Full local `make verify` | **107 passed, 2 skipped, 1 warning** on Python 3.13.0. Skips are PostgreSQL/MySQL service integration tests without local DSNs. Warning is the existing Starlette/httpx deprecation. |
| Compile / JS / shell / CLI | Passed through `make verify`: Python `compileall`, bundled Node `--check`, shell syntax, and CLI help |
| SQLite source-to-target | Passed: 2 source tables, 50 generated rows, validation 100.0, target counts 25/25, zero broken FKs, source unchanged |
| Python 3.11 / 3.12 | Mission-start runs `36918266093` and `36918274481` passed on `5e55c83243e742938776673abee96816ab1e75c6`; refreshed exact-head runs pending |
| PostgreSQL / MySQL | Disposable service integrations passed at the mission-start SHA in runs above; refreshed exact-head service runs pending |
| SQLite / backup | Local SQLite roundtrip passed; backup/restore tests **5 passed**, including WAL snapshot, integrity, artifact relocation and traversal checks |
| Connector contracts | Seven deterministic contract tests passed for SQL Server, Oracle, Snowflake, BigQuery, Redshift and uppercase MySQL metadata. Two live service tests skipped locally. No live external enterprise certification claimed. |
| Browser / Playwright | Actual-server HTTPS Chromium checks are included in the full suite and passed. They cover administrator/operator dashboards, session restore/expiry, logout, CSRF, keyboard/focus/labels, desktop/390px overflow and secret non-reflection. |
| Approval / dry-run | Passed: production dry-run does not need or consume approval and does not mutate/create the SQLite target; actual writes require exact one-use approval; destructive confirmation remains enforced |
| Agentic local | Deterministic workflow and persisted traces pass. Mandatory plan gates survive untrusted output; malformed output and provider timeout fall back safely; relational PK/FK/date/enum repair is revalidated and persisted. Refund-overpayment repair remains unsupported and fails closed at the quality gate. |
| Parquet | Included in the full local suite with installed Parquet extra; passed |
| 100k / 1M benchmark | Pending execution at a clean integrated SHA. The CLI now records exact source SHA, dirty state, Python/platform, format, seed, batch size, elapsed time, throughput, peak RSS with OS-specific units, and output size. |
| Dependency audit / secret scan | Passed at mission-start SHA only; refreshed exact-head hosted results pending |
| Production image / HTTP smoke | Passed at mission-start SHA only. Updated hosted job will record source SHA, image config digest, UTC build date, Python version, and application version; refreshed exact-head run pending |
| Deployment / live URL | `DEPLOYMENT_READY_EXTERNAL_CREDENTIALS_REQUIRED`. No authorized host project or deployment credentials were available; no deployment or URL is claimed. |
| External AI | Not live-tested; deterministic local operation is the supported fallback |
| Tracker workbook | Not modified; no canonical workbook was present in the repo, attachment, or bounded Documents search. Proposal is in `PROJECT_TRACKER_UPDATE.md`. |
| Independent review / review threads | Final exact-head independent review and resolution of the two valid GitHub review threads remain pending |
| Merge | Explicitly authorized by the user, but waiting on final exact-head CI, required independent approval, and branch protection |

## Non-claims

This candidate does not claim a managed deployment, public URL, live certification for unexecuted connectors, external AI certification, horizontal scaling, HIPAA, GDPR, or SOC 2 certification. SQLite remains the authoritative single-node state store. Local/browser tests and disposable CI services do not certify a customer or managed external environment.
