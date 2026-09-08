# Completion Matrix — v0.5

This matrix separates implementation from external runtime certification. Percentages are not inflated when credentials or vendor runtimes are unavailable.

| Capability | Implementation | Local/runtime evidence | External gate |
|---|---:|---|---|
| Core/single/multi-table generation | 100% | automated tests + live local runs | none |
| DDL / JSON Schema / OpenAPI / Avro ingestion | >=95% | parser/unit tests | vendor edge-corpus expansion remains ongoing |
| Natural-language modeling | >=95% local path | deterministic hospitality model tests | external LLM smoke pending |
| Validation / repair | >=95% | forced failure -> repair -> revalidation tests | none |
| Exports / ZIP | >=95% | artifact/API tests | Parquet requires pyarrow runtime |
| SQLite connector/load | 100% | real read-only source -> target roundtrip | none |
| PostgreSQL/MySQL/SQL Server/Oracle/Snowflake/BigQuery/Redshift | implemented | connector contract/catalog tests | real credential-gated smoke pending |
| Profiling/correlations/business semantics/privacy | >=95% implementation | unit/integration tests | source-specific fidelity can be tuned per system |
| Streaming generation | >=95% | 1,000,000-row bounded-memory benchmark | none |
| Projects/recipes/versioning/jobs/agent traces | >=95% | persistence/API tests | none |
| Agentic orchestration | >=95% | planner safety, trace, quality gate, repair, cancellation tests | external LLM smoke pending |
| Security/observability | >=95% local application scope | secret redaction, health/readiness/metrics/audit tests | production deployment hardening is environment-specific |
| UI/UX | >=95% local product shell | rendered Agent Runs/Settings + JS checks | full localhost browser E2E depends on browser environment |

SyntheticForge is not declared globally complete until the external gates in `EXTERNAL_VERIFICATION.md` are executed successfully.
