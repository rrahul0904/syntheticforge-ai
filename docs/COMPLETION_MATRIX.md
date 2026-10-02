# v1.0.0-rc1 completion matrix

| Capability | Implementation state | Current evidence | Remaining gate |
|---|---|---|---|
| Deterministic generation and schema imports | Implemented | Full local suite; SQLite roundtrip; actual-server browser flow | Final exact-head hosted CI |
| Relational generation, validation and repair | Implemented | Mandatory agent gates, malformed-plan and timeout fallback, persisted repair/revalidation; 49 focused tests passed | Refund-overpayment repair is unsupported and safely fails the quality gate |
| SQLite source introspection and approved target load | Implemented | Local source immutability, dry-run, approval, replay, destructive confirmation and roundtrip tests | Final exact-head CI |
| PostgreSQL and MySQL | Implemented | Contract tests; disposable CI runtime tests passed at starting SHA | Repeat disposable service tests on final PR head |
| SQL Server, Oracle, Snowflake, BigQuery, Redshift | Implemented | Deterministic mocked contract tests; BigQuery SDK client bug fixed | Authorized live services/credentials; production source policy remains fail-closed where enforcement is unverified |
| Projects, recipes, dataset versions and persisted agent traces | Implemented | SQLite persistence and API tests | Verify exact source SHA via final hosted container run |
| Production auth, role controls and approval | Implemented | Security/red-team tests, administrator/operator Playwright, exact one-use approval and non-consuming dry-run | Final exact-head CI and valid review-thread resolution |
| Browser interface | Implemented | Actual HTTPS Uvicorn/Chromium; login, session, CSRF, dashboard, keyboard, responsive layout and secret non-reflection | Final exact-head CI |
| Parquet | Implemented as optional runtime | Runtime tests pass in full local suite; pinned in production lock | Final exact-head dependency audit and image smoke |
| Backup and restore | Implemented | Five tests cover SQLite WAL snapshot, integrity manifest, artifact relocation, locking and archive safety | Real container-volume restore remains untested |
| Streaming and scale | Implemented for bounded single-table Python stream writers | Reproducible benchmark CLI; 100k/1M measurements pending | Run on integrated clean SHA; no production SLA claim |
| Production container | Implemented | Pinned base, non-root user, one worker, persistent `/data`, health probe; CI records image evidence | Refresh build/smoke and image digest at final exact head |
| Managed deployment | Configuration ready | External credential gate recorded | Authorized host, secrets, persistent volume, HTTPS endpoint and runtime verification |

No percentage is used because live-service and deployment gates require actual external evidence. See `V1_RELEASE_EVIDENCE.md` and `CONNECTOR_CERTIFICATION.md` for exact commit and per-connector boundaries.
