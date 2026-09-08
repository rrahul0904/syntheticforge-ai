# SyntheticForge AI v0.3 — Current Status

## Working now

- Local FastAPI application and operator-style web UI
- Single-table metadata inference and generation
- Multi-table system generation
- SQL DDL parsing
- JSON catalog / JSON Schema parsing
- OpenAPI 3.x component-schema parsing
- Avro record schema parsing
- CSV sample profiling
- Natural-language built-in domain inference for hospitality, e-commerce, SaaS and generic applications
- Read-only SQLite live metadata introspection (no source rows copied)
- Primary-key generation
- Single-column foreign-key preservation
- `_id` relationship inference when explicit FK declarations are absent
- Dependency-ordered generation
- Known date-range coherence
- Exact scenario percentages such as `12% cancellations`
- Scenario-driven missing/null percentages
- Automatic validation report:
  - NOT NULL
  - PK uniqueness
  - FK integrity
  - date coherence
  - requested scenario percentages
- CSV / JSON / NDJSON / SQL exports
- Whole-system ZIP package with manifest and validation report
- CLI
- Optional Ollama / OpenAI-compatible single-table AI schema inference
- Deterministic seeded generation
- 17 automated tests passing

## Not yet literal "any system"

The canonical model makes broader support feasible, but these adapters and behaviors are still pending:

- live PostgreSQL metadata connector
- live MySQL metadata connector
- live SQL Server metadata connector
- live Oracle metadata connector
- live Snowflake / Redshift / BigQuery catalog connector
- Kafka Schema Registry
- Protobuf
- AsyncAPI
- GraphQL schema ingestion
- XML/XSD
- Parquet schema metadata
- dbt artifacts / manifest ingestion
- Salesforce and other SaaS metadata adapters
- composite PK/FK solving
- SQL CHECK constraint execution
- cyclic FK constraint solving
- correlated multi-column and cross-table distributions
- high-volume streaming generation beyond in-memory API limits
- direct loading into isolated test databases / queues / object stores
- saved reusable projects and recipes
- LLM-based whole-system planning and rule compilation

## Current blockers

There is **no blocker to running the current local v0.3 build**.

To implement and genuinely verify live enterprise connectors, test credentials/endpoints or representative local test instances are required for the relevant database engines. Optional external LLM behavior also requires a configured provider endpoint/model; the local fallback remains functional without it.
