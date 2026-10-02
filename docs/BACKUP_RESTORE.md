# SQLite state backup and restore

SyntheticForge's production state is a single-node SQLite database under `SYNTHETICFORGE_HOME` (the production container uses `/data`). Dataset export files live below `datasets/` in that same directory. The database contains references to those artifact paths, so preserve both together.

The supported helper is `python -m app.state_backup`. It writes a ZIP containing a transactionally consistent SQLite snapshot, dataset artifacts, a SHA-256 integrity manifest, the application SHA when supplied, and names of deployment configuration variables to reapply. The offline DB copy is checkpointed and normalized to DELETE journal mode, so the archive needs only `state.db` and never packages transient `-wal` or `-shm` sidecars. Restore validates that the archive members exactly match the integrity manifest. It does not export environment-variable values, password hashes, API token digests, or the process lock. Database rows can contain application data and should be handled with the same access controls as the live volume. The helper uses SQLite's backup API (safe with WAL) and requires the production process lock to be free; stop the service first so the database and artifact set are quiescent.

## Host-managed state directory

Stop the service, then create and verify a backup outside the state directory:

```sh
python -m app.state_backup backup \
  --state-dir /srv/syntheticforge \
  --output /srv/backups/syntheticforge-$(date -u +%Y%m%dT%H%M%SZ).zip
```

Restore only into a new or empty directory (an empty mounted volume root is supported). The helper validates archive paths, file hashes, and SQLite integrity, then relocates persisted artifact references to the destination directory:

```sh
python -m app.state_backup restore \
  --archive /srv/backups/syntheticforge-20261001T120000Z.zip \
  --destination /srv/syntheticforge-restored
```

Review and apply the original deployment configuration from the operator's secret manager or protected deployment configuration. The archive records only configuration variable names, never their values; supply password hashes, token digests, proxy/CORS settings, and other secrets independently. Point a stopped instance at the restored directory, preserve its single-worker/single-replica settings, then start it and verify the health endpoint and representative project, dataset, and artifact reads before switching traffic.

## Docker named volume

For the production Compose volume, stop the app before backup. The image includes the helper, which can run with the persistent volume and a host backup directory mounted:

```sh
docker compose -f docker-compose.production.yml stop syntheticforge
mkdir -p ./backups
docker run --rm \
  --mount source=syntheticforge-data,target=/data \
  --mount type=bind,source="$PWD/backups",target=/backups \
  syntheticforge-ai:1.0.0-rc1 \
  python -m app.state_backup backup --state-dir /data --output /backups/state.zip
```

Restore by creating a fresh named volume, mounting it writable at the image's owned `/data` path, and running the same image:

```sh
docker volume create syntheticforge-data-restored
docker run --rm \
  --mount type=bind,source="$PWD/backups",target=/backups,readonly \
  --mount source=syntheticforge-data-restored,target=/data \
  syntheticforge-ai:1.0.0-rc1 \
  python -m app.state_backup restore --archive /backups/state.zip --destination /data
```

The restore destination must be empty. Before using the restored volume, update the deployment's volume selection deliberately, reapply protected configuration, and validate the application. Keep the original volume and backup until restore validation is complete. These commands are operational examples, not a deployment or promotion.

## Runtime durability and limits

- Repository connections enable foreign keys, a 30-second busy timeout, and `synchronous=FULL`; initialization selects WAL mode. WAL improves reader/writer coexistence but does not make concurrent application replicas safe.
- The production repository takes an exclusive `flock` on `state.db.single-node.lock`. A second production process opening the same state directory fails. Do not delete the lock file while the process is running; the lock is held by the process descriptor, not by the file's continued existence.
- The production image and entrypoint use one Uvicorn worker, and Compose declares one replica. Keep one application process and one replica per writable state directory. The lock is local-filesystem coordination; do not treat it as a distributed lock or place SQLite on a network filesystem.
- SQLite commit durability depends on the host filesystem and storage honoring flushes. Backups on separate storage and periodic restore drills are still required. The helper creates point-in-time DB snapshots, but the service must be stopped to align that snapshot with its artifact directory.
- In-flight generation work is in process. A restart can leave a persisted job in an intermediate state; this release does not provide durable queue recovery, automatic job resumption, or multi-process scheduling.
- This path is suitable for a single-node, persistent-volume deployment. It is not evidence of horizontal scalability, high availability, automated backup scheduling, or disaster-recovery objectives.

## Verification

`tests/test_state_backup.py` exercises backup and restore with temporary state directories and subprocess CLI calls. It checks artifact relocation, integrity validation, secret environment-value exclusion, active-process lock refusal, occupied-destination protection, and archive path traversal rejection. It does not start Docker or verify any particular volume provider.
