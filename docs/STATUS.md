# SyntheticForge AI 1.0.0-rc1 — Current Status

## Release state

SyntheticForge AI is a v1.0.0-rc1 candidate. The release retains local-first deterministic operation and adds a fail-closed single-node production mode. It is not deployed and the external CI, PostgreSQL/MySQL service, and full browser certification gates are pending.

Current local verification on the checked-in code line:

- Baseline before this implementation: `pytest` **61 passed**
- Current full `make verify`: **68 passed, 2 skipped, 1 warning** (the two live database service cases are skipped locally without service DSNs)
- Python `compileall`: **PASS**
- JavaScript syntax: **PASS**
- SQLite read-only source → profile → generate → validate → target-load roundtrip: **verified**
- Production HTTPS browser journey and same-volume app restart persistence: **passed locally**
- Parquet generation and streamed batch reload with nested/null values: **passed locally**
- 1,000,000-row streaming benchmark: baseline completed; reported RSS was incorrect on macOS and the conversion fix is pending a rerun
- Agent failure → repair → revalidation: **verified**
- Agent planner safety normalization and persisted traces: **verified**
- Agent Runs and AI Settings UI boundary checks: **verified**

## Implemented in the v1 candidate

- Core single-table and multi-table relational generation
- Composite PK/FK handling, dependency ordering, scenario percentages, edge/negative testing
- DDL, JSON Schema, OpenAPI 3.x, Avro, CSV-sample, and natural-language ingestion/modeling
- Deterministic generation plus optional external AI-assisted planning/modeling/repair
- Validation and repair for nullability, uniqueness, PK/FK integrity, ranges, enums/domains, evaluable CHECKs, dates, scenario percentages, distributions, correlations, and business rules
- CSV, JSON, JSONL/NDJSON, SQL, optional Parquet, and whole-system ZIP exports
- SQLite, PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, BigQuery, and Redshift connector adapters
- Read-only source introspection/profiling and explicit approved target loading
- Streaming/batched generation paths
- Projects, reusable recipes, immutable dataset versions, comparison, jobs, retries/cancellation, audit events, and persisted agent traces
- Privacy/sensitive-field classification and secret redaction
- Health/readiness/metrics endpoints and local operational observability
- Local operator UI for generation, projects, datasets, jobs, agent runs, and AI settings
- CLI and hardened production container packaging
- PBKDF2 admin/operator login, hashed API bearer tokens, CSRF, login throttling, immutable one-use write approvals, connector policy receipts, fail-closed production startup, and single-process SQLite lock
- Pinned runtime dependency lock, non-root container user, health check, persistent `/data`, and production Compose configuration

## Remaining certification gates

These require execution in CI or with externally supplied services/credentials:

1. Run real credential-gated smoke tests against the requested enterprise database engines:
   - PostgreSQL
   - MySQL
   - SQL Server
   - Oracle
   - Snowflake
   - BigQuery
   - Redshift
2. Run one external AI-provider smoke test using a user-provided provider endpoint/model/API key.
3. Run the configured CI workflow on GitHub, including Python 3.11/3.12, PostgreSQL/MySQL service integration, secret/dependency scans, and production image build/smoke.
4. Rerun the corrected million-row benchmark in a low-load window.
5. Provide a managed-container host and secrets for an externally verifiable deployment.

Use the commands documented in `docs/EXTERNAL_VERIFICATION.md`:

```bash
python scripts/verify_external_connectors.py
python scripts/verify_ai_agent.py
```

For Parquet:

```bash
pip install -e '.[parquet]'
```

## Completion boundary

The candidate does not claim universal certification. PostgreSQL/MySQL database services are configured as CI integration gates; only a green hosted run can certify those run results. No external AI-provider smoke or production deployment is claimed.

Broader future adapters such as Kafka Schema Registry, Protobuf, AsyncAPI, GraphQL schema ingestion, XML/XSD, dbt artifacts, and SaaS-specific metadata adapters are product-expansion work and are outside this release boundary.

Nothing in this release requires Vercel deployment.
