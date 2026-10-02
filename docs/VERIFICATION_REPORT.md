# Verification report — v1.0.0-rc1 candidate

The integrated implementation at code SHA `ab7c6644b692969217c2b4fcab2ed8fec008c3a3` passed local `make verify` on Python 3.13: **107 passed, 2 skipped, 1 warning**. PostgreSQL/MySQL service tests skipped locally because no DSNs were configured. The suite also ran Python compilation, browser JavaScript syntax through the bundled Node runtime, shell syntax, CLI help, and a SQLite source-to-target roundtrip with 50 rows, 100.0 validation quality, 0 broken foreign keys, and an unchanged source.

Mission-start hosted runs `36918266093` and `36918274481` passed on earlier SHA `5e55c83243e742938776673abee96816ab1e75c6`, including Python 3.11/3.12, disposable PostgreSQL/MySQL integrations, dependency audit, secret scan, and Docker build/HTTP smoke. Those runs do not certify the integrated candidate. Refreshed exact-head hosted checks and final release evidence are required before merge.

See [V1_RELEASE_EVIDENCE.md](V1_RELEASE_EVIDENCE.md) for current local results and pending hosted/external gates. The CI workflow runs `make verify` on Python 3.11 and 3.12 and has separate PostgreSQL/MySQL service and production-container jobs.
