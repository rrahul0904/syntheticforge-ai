# SyntheticForge AI 0.5 — Local Agentic Synthetic Data Platform

SyntheticForge AI is a local-first synthetic test-data platform. Give it a schema, API contract, sample, live read-only database, or plain-English system goal and it can model the system, profile bounded source samples, classify sensitive fields, infer rules, generate relational synthetic data, validate it, repair failed output, version/package the result, and optionally load an approved target.

SyntheticForge is **not AGI** and does not claim to understand every conceivable system. It is a structured enterprise-data agent with deterministic safety and validation gates.

## Start locally

```bash
unzip syntheticforge-ai-v0.5-agentic.zip
cd syntheticforge-ai-v0.5-agentic
./run_local.sh
```

Windows:

```bat
run_local.bat
```

Then open `http://127.0.0.1:8000`. API docs are at `http://127.0.0.1:8000/docs`.

Nothing is deployed to Vercel.

## Agentic workflow

Open **Agent runs**, enter a goal such as:

> Create a realistic hospitality QA environment with hotels, rooms, guests, reservations, payments and cancellations. Make 12% of reservations cancelled and preserve payment/date consistency.

The persisted agent executes a constrained loop:

```text
GOAL
  ↓
PLAN
  ↓
inspect/model source
  ↓
profile bounded samples
  ↓
classify PII/PHI/secrets
  ↓
infer business rules
  ↓
generate relational dataset
  ↓
validate quality
  ↓
quality below gate? ── yes → REPAIR → revalidate/regenerate ─┐
  │                                                          │
  └────────────────────────────── no / quality reached ◀──────┘
  ↓
version + package
  ↓
optional target load → HUMAN APPROVAL → load
```

Every plan/tool/observation/validation/repair/result event is persisted. The LLM may propose a plan or repair rules, but it cannot remove mandatory privacy/validation steps or bypass the target-write approval gate.

## AI is optional

The complete deterministic agent loop works without an external model. To enable AI-assisted planning/modeling/repair, use **Settings → AI Provider** or environment variables:

```bash
AI_PROVIDER=openai-compatible
AI_MODEL=<model-id>
AI_BASE_URL=<provider-base-url>
AI_API_KEY=<key>
```

Ollama is also supported. Keys entered in the UI are **process-session only**: they are not written to the SyntheticForge state database and are never returned by the status API.

## Inputs

- SQL DDL: PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, BigQuery-compatible DDL, Redshift, SQLite
- JSON Schema / canonical JSON catalog
- OpenAPI 3.x
- Avro
- CSV samples
- Natural-language system descriptions
- Live read-only database metadata/profiling

## Connectors

Implemented connector adapters:

- SQLite — locally runtime verified
- PostgreSQL
- MySQL
- SQL Server
- Oracle
- Snowflake
- BigQuery
- Redshift

Install connector drivers as needed:

```bash
pip install -e '.[postgres]'
pip install -e '.[mysql]'
pip install -e '.[sqlserver]'
pip install -e '.[oracle]'
pip install -e '.[snowflake]'
pip install -e '.[bigquery]'
pip install -e '.[redshift]'
```

Or:

```bash
pip install -e '.[all-connectors]'
```

Sources are read-only by default. Target loading requires a separate connection with `read_only=false`; truncate operations require additional explicit destructive confirmation.

## Generation and validation

SyntheticForge supports:

- deterministic seeded generation
- single- and multi-table systems
- composite PKs/FKs
- dependency ordering
- distribution-aware generation
- numeric and conditional categorical correlations
- cross-column/date/business semantics
- explicit edge/negative testing mode
- privacy-aware synthetic replacement
- streaming CSV/JSONL and batched SQL
- CSV, JSON, JSONL/NDJSON, SQL, Parquet (optional `pyarrow`), ZIP packages
- direct target loading

Validation includes NOT NULL, PK/unique, FK/composite FK, enum/domain, ranges, string lengths, evaluable CHECKs, date ordering, scenario percentages, business rules, distribution similarity and correlation similarity.

## Persistent local control plane

The UI/API/CLI share the same services for:

- projects
- reusable recipes
- immutable dataset versions
- dataset comparison
- generation jobs and retry/cancel
- persisted agent runs and traces
- audit events
- readiness/health/Prometheus-style metrics

## CLI

Examples:

```bash
syntheticforge agent \
  --goal 'Create a hospitality QA system with 12% cancellations' \
  --rows 50

syntheticforge generate --recipe hotel.yaml
syntheticforge inspect --connector postgres --connection-env SYNTHETICFORGE_SOURCE
syntheticforge profile --project hotel
syntheticforge validate --dataset <dataset-id>
syntheticforge export --dataset <dataset-id> --format parquet
syntheticforge load --dataset <dataset-id> --target postgres
```

Run `syntheticforge --help` for the commands available in this build.


## Engineering workflow

The repository includes repeatable local and CI checks:

```bash
make install-dev
make verify
make demo
```

GitHub Actions runs the test suite on Python 3.11 and 3.12, compiles Python modules, checks the browser JavaScript syntax, executes the SQLite source-to-target roundtrip, and verifies the CLI entry point. See `docs/DEVELOPMENT.md` for the development and release workflow.

## Verification in this build

Current local evidence:

- 61 automated tests passing
- agent failure → repair → revalidation test passing
- AI-planner safety normalization test passing
- all 8 connector adapters covered by catalog/contract tests
- real SQLite read-only source → profile → generate → validate → target load roundtrip: 100.0 quality, 0 broken FKs, source unchanged
- 1,000,000-row streaming benchmark: 7.658 s, ~130,580 rows/s, ~100.66 MB peak RSS in this sandbox run
- Chromium UI boundary test: Agent Runs + Settings, 0 console errors, secret not rendered
- JavaScript syntax and Python compile checks passing

Real credential-gated verification commands:

```bash
python scripts/verify_external_connectors.py
python scripts/verify_ai_agent.py
```

See `docs/VERIFICATION_REPORT.md`, `docs/COMPLETION_MATRIX.md`, `docs/SECURITY.md`, and `docs/AGENTIC_AI.md`.

## Important completion boundary

The implementation is intentionally not marked fully complete until the credential-gated real-system smoke tests are executed for the requested enterprise connectors and a real external AI provider, and optional Parquet runtime is exercised with `pyarrow`. See `docs/EXTERNAL_VERIFICATION.md` for the exact final inputs needed.
