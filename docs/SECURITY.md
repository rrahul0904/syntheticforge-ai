# Security Boundary

SyntheticForge is local-first. Source database connectors default to `read_only=true`, connector identifiers are validated before interpolation, and target loading requires a separate configuration with `read_only=false`. Destructive truncate/load additionally requires explicit confirmation.

Secrets are accepted through environment configuration or process-session AI settings. AI API keys are not returned from provider-status endpoints and are not written to projects, recipes, agent runs, or audit events. Connector errors redact configured usernames/passwords before returning diagnostics.

The application does not claim HIPAA, GDPR, SOC 2, or other regulatory certification. PII/PHI classification is a risk-reduction mechanism and must be validated against organizational policy before production use.

Production mode (`SYNTHETICFORGE_ENV=production`) fails startup without a PBKDF2 admin password hash, hashed API bearer token, explicit HTTPS CORS origins, and the source commit SHA baked into the image. It uses expiring opaque sessions, CSRF tokens, persistent login throttling, admin/operator gates, security response headers, bounded request bodies, a one-worker/one-replica SQLite lock, and append-only audit, connector receipt, and single-use write-approval evidence. An administrator must re-enter the password to approve an exact direct or agent target write; approvals expire and reject mismatches or replays.

Production source access is currently allowed only for SQLite, PostgreSQL, MySQL, and Oracle adapters that declare a database-enforced read-only mechanism. SQLite uses URI `mode=ro`; PostgreSQL and MySQL set and verify read-only transactions; Oracle begins a read-only transaction. Production fails closed for other adapters until their read-only policy can be proven. The production image bundles PostgreSQL/MySQL drivers and Parquet; other adapters need optional drivers in a separately built image.

Repository safety expectations:
- never commit `.env` files or local state databases;
- use read-only source accounts where the database supports them;
- use disposable credentials for integration tests;
- do not expose development mode publicly;
- terminate public traffic with HTTPS and configure only trusted proxy IPs/CIDRs for forwarded headers;
- keep production on one replica and one worker while SQLite remains authoritative;
- mount and back up `/data`; make sure the mounted volume is writable by container UID 10001;
- keep hosting credentials and generated hashes in a secret manager, never in the image or repository.
