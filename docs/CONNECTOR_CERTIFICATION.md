# Connector certification

“Implemented”, “contract-tested”, “local-integration-tested”, and “live-certified” are separate evidence levels. A green unit or mocked test does not promote a connector to live certification.

| Connector | Implemented | Contract-tested | Local integration-tested | Live-certified | Production source policy |
|---|---|---|---|---|---|
| SQLite | Yes | Yes | Yes: repository demo and target-load tests | No external service | Read-only URI for sources; writable targets are separate |
| PostgreSQL | Yes | Yes | Pending PostgreSQL CI service run | No | Session read-only mode and verification |
| MySQL | Yes | Yes | Pending MySQL CI service run | No | Autocommit plus read-only transaction |
| SQL Server | Yes | Yes | Not run here | No | Denied in production until DB-enforced mode is implemented and proven |
| Oracle | Yes | Yes | Not run here | No | Read-only transaction adapter; no live instance available |
| Snowflake | Yes | Yes | Not run here | No | Denied in production until DB-enforced mode is implemented and proven |
| BigQuery | Yes | Yes | Not run here | No | Denied in production for source access until enforcement is proven |
| Redshift | Yes | Yes | Not run here | No | Denied in production until DB-enforced mode is implemented and proven |

The production image includes PostgreSQL and MySQL drivers and the Parquet runtime. Other adapters remain available in local/development installs when their optional drivers are installed. Live cloud/enterprise certification requires authorized credentials and must record the service/version, date, scenario, and result here.

CI service-container tests and live credential-gated smoke tests are separate gates. A pending or skipped external check must remain labeled pending; it is never interpreted as a pass.
