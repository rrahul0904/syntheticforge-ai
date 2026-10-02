# Connector certification

Connector implementation, adapter contracts, disposable service tests, and live external certification are separate claims. Mock-based contract tests do not establish live service compatibility.

## Current integrated evidence

The connector changes are integrated through code commit `bd0136f` (contract tests and BigQuery client fix). The full local suite passed later at `ab7c6644b692969217c2b4fcab2ed8fec008c3a3`; exact final PR-head evidence will be recorded in `V1_RELEASE_EVIDENCE.md` after hosted CI.

| Connector | Implementation | Contract evidence | Runtime evidence | Live external state | Production source policy |
|---|---|---|---|---|---|
| SQLite | `IMPLEMENTED` | `CONTRACT_TESTED` | `LOCAL_RUNTIME_TESTED`: source-to-target roundtrip, read-only source, dry-run, and target approval tests | Not applicable | Source opens read-only; target writes are separate and require approval in production |
| PostgreSQL | `IMPLEMENTED` | `CONTRACT_TESTED` | `LOCAL_RUNTIME_TESTED`: disposable PostgreSQL service integration passed at mission-start SHA; rerun at integrated exact head is pending hosted CI | `BLOCKED_EXTERNAL`: no managed/customer instance credentials supplied | Session read-only mode is set and verified |
| MySQL | `IMPLEMENTED` | `CONTRACT_TESTED`: lowercase and uppercase metadata-key normalization, foreign keys, and original row-key casing | `LOCAL_RUNTIME_TESTED`: disposable MySQL service integration passed at mission-start SHA; rerun at integrated exact head is pending hosted CI | `BLOCKED_EXTERNAL`: no managed/customer instance credentials supplied | Read-only transaction policy is requested; disposable CI source/write checks passed at mission-start SHA |
| SQL Server | `IMPLEMENTED` | `CONTRACT_TESTED`: mocked information-schema discovery, PK/FK metadata, parameterization, quoting, bounded sample query | Not runtime-tested | `BLOCKED_EXTERNAL`: no authorized instance or credentials | Production source access fails closed until read-only enforcement is verified |
| Oracle | `IMPLEMENTED` | `CONTRACT_TESTED`: mocked catalog, PK/FK/unique/check metadata, identifier behavior, read-only transaction request | Not runtime-tested | `BLOCKED_EXTERNAL`: no authorized instance or credentials | Adapter begins a read-only transaction; no live policy verification is recorded |
| Snowflake | `IMPLEMENTED` | `CONTRACT_TESTED`: mocked information-schema columns and PK/FK/unique/check behavior, bounded sampling | Not runtime-tested | `BLOCKED_EXTERNAL`: no authorized account or credentials | Production source access fails closed until read-only enforcement is verified |
| BigQuery | `IMPLEMENTED` | `CONTRACT_TESTED`: mocked SDK client discovery, nested field metadata, bounded query sampling, connection check, and nested target fail-closed behavior | Not runtime-tested | `BLOCKED_EXTERNAL`: no authorized project or credentials | Production source access fails closed until read-only enforcement is verified; nested target writes/dry-runs are rejected until row reshaping is supported |
| Redshift | `IMPLEMENTED` | `CONTRACT_TESTED`: mocked PostgreSQL-style catalog metadata and bounded sample query | Not runtime-tested | `BLOCKED_EXTERNAL`: no authorized cluster or credentials | Production source access fails closed until read-only enforcement is verified |

## Limits

The dedicated contract suite is `tests/test_connector_contracts.py`. Its seven tests use deterministic mocks and do not contact SQL Server, Oracle, Snowflake, BigQuery, or Redshift. Local PostgreSQL/MySQL service tests skip when their DSNs are absent; hosted CI provides disposable services and must pass at the final PR head before those runtime statuses are current for the release candidate.

No live connector certification, managed-service compatibility, provider-specific authentication, grants/IAM correctness, or external performance is claimed. External tests require authorized disposable or read-only resources. They must record service version, date, scenario, source immutability, bounded profile limit, secret redaction, and outcome without changing source systems.
