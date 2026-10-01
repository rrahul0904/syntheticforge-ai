# Completion matrix — v1.0.0-rc1

| Capability | Implementation state | Executed evidence | Remaining gate |
|---|---|---|---|
| Deterministic single-table and relational generation | Implemented | Existing unit/system/agent tests; local browser run pending | Final full verification |
| DDL, JSON Schema, OpenAPI, Avro and CSV ingestion | Implemented | Parser/API tests; UI browser checks pending | Final full verification |
| Validation, repair and revalidation | Implemented | Existing forced-failure repair tests | Final full verification |
| SQLite source introspection and separate target load | Implemented | `scripts/demo_sqlite_roundtrip.py`; production read-only browser flow pending | Final verification rerun |
| PostgreSQL and MySQL adapters | Implemented | Contract tests | Disposable CI source-to-target service tests |
| SQL Server, Oracle, Snowflake, BigQuery and Redshift adapters | Implemented | Contract/catalog tests | Real service/credential-gated checks; production read-only policy blocks unproven adapters |
| Projects, recipes, dataset fingerprints and artifact persistence | Implemented | Persistence and API tests | Verify exact commit SHA is present in production artifact |
| Production authentication and write controls | Implemented | Negative security tests | Final full verification and container smoke |
| Parquet | Implemented as optional runtime | `pyarrow` pinned in release image and CI extras | Generate, reload and verify content in final run |
| Browser UI | Implemented | Real HTTPS Chromium suite added | Final local pass and hosted CI result |
| Production container | Implemented | Pinned base, dependency lock, non-root, one worker, health probe | Docker build/HTTP smoke runs in hosted CI only |
| Managed deployment | Configuration ready | Deployment steps recorded | Hosting credentials, persistent volume, public HTTPS endpoint and runtime verification |

No percentages are used because remaining checks depend on runtime evidence, not estimated implementation completeness. See `V1_RELEASE_GAP_MATRIX.md` and `V1_RELEASE_EVIDENCE.md` for the scoped detail.
