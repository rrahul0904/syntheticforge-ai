# Verification report — current v1.0.0-rc1 candidate

The original v0.5 verification had 61 passing tests and a successful SQLite source-to-target demonstration. That is baseline evidence only and does not certify the modified candidate.

The current release-gate results are maintained in [V1_RELEASE_EVIDENCE.md](V1_RELEASE_EVIDENCE.md). That record distinguishes completed local runs from pending browser, container, CI-service, credential-gated connector, AI-provider, benchmark, and deployment checks. Do not use the historical report to infer current status.

The single developer certification command is `make verify`. CI runs it on Python 3.11 and 3.12 after installing the browser runtime, and has separate PostgreSQL/MySQL integration and production-container jobs.
