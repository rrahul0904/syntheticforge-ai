# SyntheticForge AI 1.0.0-rc1 — Current Status

## Release state

SyntheticForge AI is a v1.0.0-rc1 candidate. Application code passes local `make verify` at `7d25c124b09bc540c7e928de345c434640b8e3b4`; hosted code, service and container checks passed at `84be249c1e58c4f83b92132cb2129740f597fd91`. The current docs-only evidence update awaits its exact-head CI. Deployment and external enterprise connector/AI certification remain unavailable without authorized credentials.

Current mission evidence:

- Full local `make verify`: **110 passed, 2 skipped, 1 warning** on Python 3.13; PostgreSQL/MySQL service tests skipped because no local service DSNs were configured. Python compile, JavaScript syntax, shell syntax, CLI help, and SQLite source-to-target roundtrip passed.
- Production security and browser certification: focused live HTTPS Uvicorn/Chromium tests cover administrator and operator dashboards, role restrictions, login/session/CSRF, responsive screens, and token non-reflection. Exact per-file totals are in the release evidence.
- Agentic deterministic mode: mandatory gates, malformed/untrusted planning, provider timeout fallback, relational repair, and persisted failed-then-passing validation traces are covered.
- Backup/restore: **5 passed**, including WAL normalization, manifest integrity, relocation, lock refusal, empty destination, and traversal rejection.
- Connector contract suite passed with two PostgreSQL/MySQL service tests skipped locally; mocked cloud-adapter checks do not claim live certification. Final-head hosted service runs remain pending.
- 100k and 1M NDJSON benchmarks passed at `2ff5f1d`; results and environment are in `V1_RELEASE_EVIDENCE.md`. Hosted image and service evidence passed at `84be249`; exact-head docs-only CI remains pending.
- Operators are blocked from direct target dry-runs and source-backed agent runs before connector activity, and production SQLite file schema imports require an admin browser session; nested BigQuery target load and dry-run both fail closed until nested row reshaping is supported.
- Deployment: `DEPLOYMENT_READY_EXTERNAL_CREDENTIALS_REQUIRED`; no live URL is claimed.

The authoritative current exact-head evidence is maintained in `V1_RELEASE_EVIDENCE.md` and `NEXT_PHASE_EXECUTION_LEDGER.md`. Do not treat historic test totals or the starting-head CI as evidence for the final integrated SHA.

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
