# SyntheticForge AI Architecture

## Product boundary

SyntheticForge is a synthetic-data intelligence layer, not a claim of artificial general intelligence. Its job is to convert structured system knowledge plus human test intent into safe, realistic, reproducible test datasets.

## Current local architecture

```text
Browser / CLI
     |
     v
FastAPI application
     |
     +------------------------+
     |                        |
Schema adapters          Single-table inference
     |                        |
     v                        v
Canonical TableSpec / ColumnSpec / ForeignKeySpec graph
     |
     v
Dependency planner
     |
     v
Deterministic synthetic generator
     |
     +---- semantic value generators
     +---- relationship resolver
     +---- scenario rules
     +---- coherence rules
     |
     v
Validation engine
     +---- NOT NULL
     +---- PK uniqueness
     +---- FK integrity
     +---- date coherence
     +---- scenario percentages
     |
     v
CSV / JSON / NDJSON / SQL / system ZIP
```

## Canonical model

Every source adapter is normalized into the same internal objects:

- `TableSpec`
- `ColumnSpec`
- `ForeignKeySpec`

This is the key to supporting many source-system formats without building a separate generator for each one.

## Input adapters

### SQL DDL

The parser extracts common `CREATE TABLE` definitions, column types, nullability, primary keys, uniqueness, inline references, and table-level foreign keys.

### JSON Schema / catalog

Standard object schemas are mapped to tables. A SyntheticForge catalog can explicitly define multiple tables, columns and foreign keys.

### OpenAPI

`components.schemas` object contracts are converted into table-like entities. Obvious identifier relationships are inferred after normalization.

### Avro

Avro record fields, nullable unions, enums, and common logical types are normalized.

### CSV profiling

Headers and a bounded sample are used for type inference, null-rate estimation, primary-ID heuristics and safe categorical preservation. Personal-looking values are regenerated rather than replayed.

## Relationship planner

The planner builds dependencies from foreign keys and performs deterministic topological generation. Parent tables are generated first. Child foreign keys are then sampled only from keys that actually exist in generated parent rows.

If a relationship cycle exists, generation proceeds deterministically and emits a warning. Full cyclic constraint solving is a future capability.

## Generation

The local generator uses Faker plus deterministic pseudo-random generation. Semantic types include identifiers, people, companies, emails, phones, addresses, countries, postal codes, money, booleans, dates, timestamps, URLs, IPs, JSON-like structures and category choices.

## Scenario layer

The current scenario parser intentionally supports a small deterministic subset of natural-language directives. It can enforce percentage-based cancellation and missing-value scenarios. The architecture leaves room for a future planning agent that compiles richer natural-language intent into explicit generation constraints.

## AI adapter

An optional LLM can help infer a single-table schema from metadata. External AI is not required for generation. The current provider abstraction supports Ollama and OpenAI-compatible endpoints.

## Privacy boundary

SyntheticForge should be usable without copying production rows. Future database introspection should default to metadata-only access. Any statistical profiling mode should make sampling explicit, minimize retention, and avoid echoing direct identifiers.

## Scale path

The current API returns bounded datasets in memory. Large-scale generation should move to chunked/streaming writers and background job orchestration only once a durable local job model exists. For very large test environments, the generator should write directly to files/object stores/databases rather than materializing every row in an API response.
