# v1.0.0-rc1 release evidence

This record distinguishes local evidence, hosted candidate checks, and external gates that still require authorized credentials. The code/workflow run IDs below cover the current implementation; the evidence-doc commit receives its own final-head CI run before merge.

| Evidence | Result |
|---|---|
| Repository / PR | `rrahul0904/syntheticforge-ai`, PR #2, base `main` |
| Candidate | `1.0.0-rc1` |
| Mission start head | `5e55c83243e742938776673abee96816ab1e75c6` |
| Integrated application-code SHA | `7d25c124b09bc540c7e928de345c434640b8e3b4` |
| Benchmark run SHA | `2ff5f1d7d76438f9805c253162d4907501344b5f`; clean working tree, exact SHA emitted by the benchmark tool. The later code change only tightens production authorization for SQLite schema imports. |
| Local verification date | 2026-10-02 |
| PR state | Open; code/workflow SHA `84be249c1e58c4f83b92132cb2129740f597fd91` passed push CI run `37023127940` and PR CI run `37023133621`; this evidence update is docs-only |
| Full local `make verify` | **110 passed, 2 skipped, 1 warning** on Python 3.13.0 for code `7d25c12`. Skips are PostgreSQL/MySQL service integration tests without local DSNs. Warning is the existing Starlette/httpx deprecation. |
| Compile / JS / shell / CLI | Passed through `make verify`: Python `compileall`, bundled Node `--check`, shell syntax, and CLI help |
| SQLite source-to-target | Passed: 2 source tables, 50 generated rows, validation 100.0, target counts 25/25, zero broken FKs, source unchanged |
| Python 3.11 / 3.12 | Passed at `84be249` in push run `37023127940` and PR run `37023133621`, including `make verify`, browser tests, and `pip-audit` |
| PostgreSQL / MySQL | Disposable source-to-target service integrations passed at `84be249` in runs `37023127940` and `37023133621` |
| SQLite / backup | Local SQLite roundtrip passed; backup/restore tests **5 passed**, including WAL snapshot, integrity, artifact relocation and traversal checks |
| Connector contracts | Eight deterministic contract tests passed for SQL Server, Oracle, Snowflake, BigQuery, Redshift, uppercase MySQL metadata, and nested BigQuery target fail-closed behavior. Two live service tests skipped locally. No live external enterprise certification claimed. |
| Browser / Playwright | Actual-server HTTPS Chromium checks are included in the full suite and passed. They cover administrator/operator dashboards, session restore/expiry, logout, CSRF, keyboard/focus/labels, desktop/390px overflow and secret non-reflection. |
| Approval / dry-run | Passed: admin production dry-run does not need or consume approval and does not mutate/create the SQLite target; operators are blocked from target dry-runs, source-backed agent runs, and server-local SQLite schema imports before connector/file activity; actual writes require exact one-use approval; destructive confirmation remains enforced |
| Latest reviewer follow-up | Operator target/source-agent bypasses, nested BigQuery targets, browser login wait, and operator SQLite schema-file metadata access were addressed. Independent review at code SHA `7d25c12` found no remaining high or medium issues; focused regression checks and full local verification passed. |
| Agentic local | Deterministic workflow and persisted traces pass. Mandatory plan gates survive untrusted output; malformed output and provider timeout fall back safely; relational PK/FK/date/enum repair is revalidated and persisted. Refund-overpayment repair remains unsupported and fails closed at the quality gate. |
| Parquet | Included in the full local suite with installed Parquet extra; passed |
| 100k benchmark | Passed at `2ff5f1d`: 100,000 NDJSON rows, seed 2026, batch 10,000; 0.927536 s; 107,812.52 rows/s; peak RSS 40,210,432 bytes / 38.35 MiB; output 5,381,165 bytes; Python 3.13.0, macOS 26.5.2, x86_64, 12 CPUs. |
| 1M benchmark | Passed at `2ff5f1d`: 1,000,000 NDJSON rows, seed 2026, batch 10,000; 9.011902 s; 110,964.36 rows/s; peak RSS 40,247,296 bytes / 38.38 MiB; output 54,809,396 bytes; Python 3.13.0, macOS 26.5.2, x86_64, 12 CPUs. |
| Dependency audit / secret scan | `pip-audit -r requirements-prod.lock` and Gitleaks passed at `84be249` in push run `37023127940` and PR run `37023133621` |
| Production image / HTTP smoke | Passed at `84be249` in both runs. Source SHA `84be249c1e58c4f83b92132cb2129740f597fd91`; image ID/config digest `sha256:1de25cb33e61cc41661e69b58c70f6c158bb96573b488bef2afd6167c42e332c`; build timestamp `2026-10-02T14:54:11Z`; Python `3.12.14`; app `1.0.0rc1`. Image health, readiness, unauthenticated API rejection and UI smoke passed. |
| Deployment / live URL | `DEPLOYMENT_READY_EXTERNAL_CREDENTIALS_REQUIRED`. No authorized host project or deployment credentials were available; no deployment or URL is claimed. |
| External AI | Not live-tested; deterministic local operation is the supported fallback |
| Tracker workbook | Not modified; no canonical workbook was present in the repo, attachment, or bounded Documents search. Proposal is in `PROJECT_TRACKER_UPDATE.md`. |
| Independent review / review threads | Independent review at `7d25c12` found no remaining high or medium issues; both original GitHub review threads were fixed and are being resolved |
| Merge | Explicitly authorized by the user; awaiting evidence-doc commit CI and final repository-state check |

## Non-claims

This candidate does not claim a managed deployment, public URL, live certification for unexecuted connectors, external AI certification, horizontal scaling, HIPAA, GDPR, or SOC 2 certification. SQLite remains the authoritative single-node state store. Local/browser tests and disposable CI services do not certify a customer or managed external environment.
