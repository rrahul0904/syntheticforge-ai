# SyntheticForge AI — Capability Matrix

## Current v0.3

| Area | Current capability |
|---|---|
| Single table | Metadata-only schema inference + explicit schema generation |
| Whole system | Multi-table generation with dependency ordering |
| Relationships | Explicit single-column FK support + `_id` relationship inference |
| Input | SQL DDL, JSON catalog, JSON Schema, OpenAPI 3, Avro, CSV sample, natural-language domain description, read-only SQLite metadata |
| Targets | PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, BigQuery, Redshift, SQLite |
| Exports | CSV, JSON, NDJSON, SQL, whole-system ZIP |
| Determinism | Seeded repeatable generation |
| Business rules | Basic natural-language percentage rules such as cancellations/nulls |
| Privacy | Generated personal values; CSV category preservation is restricted to category-like fields |
| AI | Optional LLM-assisted single-table schema inference, local fallback always available |
| Interface | Local web UI, REST API, CLI |

## Next engineering waves

### Wave 0.3 — live source introspection

Read-only connectors for PostgreSQL, MySQL, SQL Server, Oracle, Snowflake and Redshift. Pull catalog metadata only by default: table definitions, keys, constraints, comments, enums, and selected non-sensitive statistics.

### Wave 0.4 — semantic distribution engine

Generate correlated values rather than independent columns. Examples: room price varies by room class and season, taxes derive from subtotal and location, refund amounts do not exceed captured payments, and order items reconcile to order totals.

### Wave 0.5 — test-scenario agent

Translate requests such as:

> Create 500,000 reservations across 40 hotels. Make 8% cancel inside the penalty window, 1% have failed payments, 0.5% produce duplicate upstream events, and preserve every referential relationship.

into an explicit generation plan and validation suite.

### Wave 0.6 — broader system contracts

Protobuf, AsyncAPI, GraphQL, XSD/XML, Parquet schema metadata, Kafka Schema Registry, Salesforce metadata, dbt artifacts, Snowflake information schema, and warehouse catalog exports.

### Wave 0.7 — validation and loading

Automatically validate generated datasets against the supplied contracts and optionally load them into isolated test databases, object storage, message queues, or API fixtures.
