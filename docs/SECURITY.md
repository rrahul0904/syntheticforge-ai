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

## Defensive verification record

On 2026-10-01, the local production-mode API/security slice passed with **41 tests passed** (one existing Starlette/httpx deprecation warning):

```text
pytest -q tests/test_red_team_security.py tests/test_production_security.py tests/test_completion_features.py tests/test_agentic.py
41 passed, 1 warning
```

Coverage includes session rotation against a caller-supplied session cookie, expiry and revocation behavior, missing and invalid CSRF tokens, operator bearer-token privilege limits, production HTTP login rejection, required production configuration and single-worker/single-replica enforcement, explicit CORS origin validation and preflight handling, malformed and oversized schema requests, unsafe SQL identifiers, approval replay/mismatch/expiry and destructive-flag binding, and provider-key/connector-secret/sensitive-row redaction. A trusted CORS preflight receives an allow-origin response without a session; an untrusted origin receives no allow-origin response; the corresponding protected resource request still returns 401 without authentication.

The SQLite schema-file input intentionally accepts a caller-supplied path and opens it read-only. Verification confirms it returns table metadata without copying row values. This is not a filesystem sandbox: anyone authorized to submit schema requests can request metadata for a SQLite file readable by the application process. Keep schema-input access within the trusted operator group and mount only data the service is allowed to inspect.

These are local unit/API checks, not an external penetration test. They do not certify deployment proxy/TLS configuration, browser behavior on a live origin, production secrets management, third-party connector security, or regulatory compliance. CORS preflight validation is exercised against configured trusted and untrusted origins; no external origin or live deployment was tested. SQLite remains a single-worker, single-replica state store, and its data directory must be protected and backed up by the deployment operator.
