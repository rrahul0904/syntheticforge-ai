# External Verification Gates

The core/local runtime is verified without external credentials. Final live smoke tests for enterprise systems require user-owned test infrastructure and credentials.

Provide connection JSON through environment variables, never by committing secrets. Suggested variables:

- `SYNTHETICFORGE_POSTGRESQL`
- `SYNTHETICFORGE_MYSQL`
- `SYNTHETICFORGE_SQLSERVER`
- `SYNTHETICFORGE_ORACLE`
- `SYNTHETICFORGE_SNOWFLAKE`
- `SYNTHETICFORGE_BIGQUERY`
- `SYNTHETICFORGE_REDSHIFT`

Each value is a JSON object accepted by `ConnectorConfig` (host/database/schema_name/username/password and vendor-specific account, warehouse, project, or `extra` values). Sources must remain read-only for verification.

Run:

```bash
python scripts/verify_external_connectors.py
```

For an external AI provider set `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, and `AI_API_KEY`, then run:

```bash
python scripts/verify_ai_agent.py
```

Parquet runtime verification additionally requires `pip install -e '.[parquet]'`.
