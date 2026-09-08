# Security Boundary

SyntheticForge is local-first. Source database connectors default to `read_only=true`, connector identifiers are validated before interpolation, and target loading requires a separate configuration with `read_only=false`. Destructive truncate/load additionally requires explicit confirmation.

Secrets are accepted through environment configuration or process-session AI settings. AI API keys are not returned from provider-status endpoints and are not written to projects, recipes, agent runs, or audit events. Connector errors redact configured usernames/passwords before returning diagnostics.

The application does not claim HIPAA, GDPR, SOC 2, or other regulatory certification. PII/PHI classification is a risk-reduction mechanism and must be validated against organizational policy before production use.

Repository safety expectations:
- never commit `.env` files or local state databases;
- use read-only source accounts where the database supports them;
- use disposable credentials for integration tests;
- do not expose the local FastAPI service publicly without adding production authentication, TLS, and deployment-specific controls.
