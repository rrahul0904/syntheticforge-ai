# SyntheticForge AI v0.5 — Current Status

## Release state

SyntheticForge AI v0.5 is implemented and locally verified as a local-first, agentic synthetic-data platform. The GitHub release tree contains the complete application source, tests, scripts, documentation, UI, connector adapters, and packaging files.

Current local verification on the checked-in code line:

- `pytest`: **61 passed**
- Python `compileall`: **PASS**
- JavaScript syntax: **PASS**
- SQLite read-only source → profile → generate → validate → target-load roundtrip: **verified**
- 1,000,000-row streaming benchmark: **verified with bounded memory**
- Agent failure → repair → revalidation: **verified**
- Agent planner safety normalization and persisted traces: **verified**
- Agent Runs and AI Settings UI boundary checks: **verified**

## Implemented in v0.5

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
- CLI and Docker/local-run packaging

## Remaining final certification gates

These are **external runtime verification gates**, not missing implementation:

1. Run real credential-gated smoke tests against the requested enterprise database engines:
   - PostgreSQL
   - MySQL
   - SQL Server
   - Oracle
   - Snowflake
   - BigQuery
   - Redshift
2. Run one external AI-provider smoke test using a user-provided provider endpoint/model/API key.
3. Exercise the optional Parquet export path in a runtime with `pyarrow` installed.

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

The **v0.5 implementation and local verification are complete for the defined release scope**. Global/enterprise certification is intentionally not labeled 100% until the external credential-gated connector and AI-provider smoke tests above pass.

Broader future adapters such as Kafka Schema Registry, Protobuf, AsyncAPI, GraphQL schema ingestion, XML/XSD, dbt artifacts, and SaaS-specific metadata adapters are product-expansion work and are not part of the v0.5 release acceptance boundary.

Nothing in this release requires Vercel deployment.
