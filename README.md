# SyntheticForge AI 1.0.0-rc1 — Local Agentic Synthetic Data Platform

SyntheticForge AI is a local-first synthetic test-data platform. Give it a schema, API contract, sample, live read-only database, or plain-English system goal and it can model the system, profile bounded source samples, classify sensitive fields, infer rules, generate relational synthetic data, validate it, repair failed output, version/package the result, and optionally load an approved target.

SyntheticForge is **not AGI** and does not claim to understand every conceivable system. It is a structured enterprise-data agent with deterministic safety and validation gates.

## Start locally

```bash
git clone https://github.com/rrahul0904/syntheticforge-ai.git
cd syntheticforge-ai
python -m pip install -e '.[dev,parquet]'
./run_local.sh
```

Windows:

```bat
run_local.bat
```

Then open `http://127.0.0.1:8000`. API docs are at `http://127.0.0.1:8000/docs`.

Nothing is deployed to Vercel.

For the release boundary, deployment status, and exact verification evidence, see [the v1 release evidence](docs/V1_RELEASE_EVIDENCE.md), [connector certification matrix](docs/CONNECTOR_CERTIFICATION.md), and [deployment instructions](docs/DEPLOY_NOW.md). The current release candidate is single-node SQLite-backed and is not a claim of external deployment or universal connector certification.

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

## Verification

Verification claims are recorded with their exact source commit in [`docs/V1_RELEASE_EVIDENCE.md`](docs/V1_RELEASE_EVIDENCE.md). The current implementation, test totals, hosted CI, image, benchmark, deployment, and external-service status are tracked separately there. Connector status distinguishes contract coverage, disposable runtime tests, and live external certification in [`docs/CONNECTOR_CERTIFICATION.md`](docs/CONNECTOR_CERTIFICATION.md). See [`docs/NEXT_PHASE_EXECUTION_LEDGER.md`](docs/NEXT_PHASE_EXECUTION_LEDGER.md) for integration and review evidence.

Real credential-gated verification commands:

```bash
python scripts/verify_external_connectors.py
python scripts/verify_ai_agent.py
```

See `docs/VERIFICATION_REPORT.md`, `docs/COMPLETION_MATRIX.md`, `docs/SECURITY.md`, and `docs/AGENTIC_AI.md`.

## Important completion boundary

The implementation is intentionally not marked fully complete until the credential-gated real-system smoke tests are executed for the requested enterprise connectors and a real external AI provider, and optional Parquet runtime is exercised with `pyarrow`. See `docs/EXTERNAL_VERIFICATION.md` for the exact final inputs needed.
