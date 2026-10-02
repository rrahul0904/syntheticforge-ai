# Deploy SyntheticForge AI v1.0.0-rc1

Status: `DEPLOYMENT_READY_EXTERNAL_CREDENTIALS_REQUIRED`. The local environment has Docker but no Railway or AWS CLI, linked container-host project, or configured deployment credential variables. No external runtime or live URL was available to verify; no deployment was executed.

## Required production values

Generate a password hash and a random API token locally. Store the generated values in the hosting provider’s secret manager; never commit them.

```sh
python scripts/hash_password.py
python -c 'import hashlib,secrets; token=secrets.token_urlsafe(48); print("SYNTHETICFORGE_API_TOKEN="+token); print("SYNTHETICFORGE_API_TOKEN_SHA256="+hashlib.sha256(token.encode()).hexdigest())'
```

Set these variables on the service:

```text
SYNTHETICFORGE_ENV=production
SYNTHETICFORGE_ADMIN_USERNAME=admin
SYNTHETICFORGE_ADMIN_PASSWORD_HASH=<output of hash_password.py>
SYNTHETICFORGE_API_TOKEN_SHA256=<SHA-256 digest from the command above>
SYNTHETICFORGE_APPLICATION_SHA=<40-character source commit SHA used to build the image>
SYNTHETICFORGE_CORS_ORIGINS=https://<the exact public host>
SYNTHETICFORGE_HOME=/data
SYNTHETICFORGE_REPLICAS=1
WEB_CONCURRENCY=1
SYNTHETICFORGE_TRUSTED_PROXY_IPS=127.0.0.1,::1
```

Mount a persistent volume at `/data`, writable by container UID 10001. Keep one service replica and one application worker. Terminate public TLS at the managed ingress, restrict ingress to HTTPS, and set `SYNTHETICFORGE_TRUSTED_PROXY_IPS` to the ingress peer IPs/CIDRs so Uvicorn accepts forwarded HTTPS information only from those peers. Do not set it to `*` unless the container is reachable only through a trusted TLS ingress. Configure provider-managed restart-on-failure, a reasonable CPU/memory limit, and encrypted daily backups of `/data`; test restore before relying on backups. The app health check is `/api/health`; the storage readiness check is `/api/readiness`.

## Local production-like run

After setting the required variables in the shell, launch the checked-in Compose service:

```sh
export SYNTHETICFORGE_APPLICATION_SHA="$(git rev-parse HEAD)"
docker compose -f docker-compose.production.yml up --build -d
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/readiness
```

The Compose file binds to loopback; put a TLS reverse proxy in front for a public service. Do not expose the container directly over plain HTTP. Stop it with `docker compose -f docker-compose.production.yml down` while retaining the named state volume.

## Generic host / managed container

Build from the repository root with `docker build -t syntheticforge-ai:1.0.0-rc1 .`, push the image to the host’s private registry, then run one replica with a persistent `/data` mount and the environment above. For Railway, create a Dockerfile-based service, mount its persistent volume at `/data`, set the secrets and variables above, expose the service through its HTTPS domain, and configure one replica. Do not deploy with Vercel. Confirm the service reports healthy before using it.

After the secret setup, verify externally: HTTPS health and readiness; unauthenticated API denial; browser login; deterministic generation/validation; ZIP download; project creation surviving a service restart; and an attempted target write denied until an exact approval is issued and consumed. Capture the deployment URL and timestamp in `V1_RELEASE_EVIDENCE.md` only after those checks pass.

## Backup and restore

Stop the service or take a provider-consistent volume snapshot before copying `/data`. Preserve the SQLite database and the artifacts directory together. Restore both to the same mounted path, start one replica, then verify `/api/readiness`, projects, datasets, and artifact downloads. Keep backups encrypted and access-controlled; the local database contains operational metadata and audit history.
