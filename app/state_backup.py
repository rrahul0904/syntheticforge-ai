"""Portable backup and restore helpers for the single-node SQLite state store."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT_VERSION = 1
CONFIG_REFERENCES = (
    "SYNTHETICFORGE_APPLICATION_SHA",
    "SYNTHETICFORGE_HOME",
    "SYNTHETICFORGE_CORS_ORIGINS",
    "SYNTHETICFORGE_ADMIN_USERNAME",
    "SYNTHETICFORGE_ADMIN_PASSWORD_HASH",
    "SYNTHETICFORGE_API_TOKEN_SHA256",
    "SYNTHETICFORGE_OPERATOR_USERNAME",
    "SYNTHETICFORGE_OPERATOR_PASSWORD_HASH",
    "SYNTHETICFORGE_SESSION_TTL_SECONDS",
    "SYNTHETICFORGE_TRUSTED_PROXY_IPS",
    "SYNTHETICFORGE_REPLICAS",
    "WEB_CONCURRENCY",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_database_portable(path: Path) -> None:
    """Checkpoint WAL state and switch this offline copy to a self-contained DB."""
    conn = sqlite3.connect(path, timeout=30)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        if mode == "wal":
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0] != 0:
                raise RuntimeError(f"Could not checkpoint SQLite backup WAL: {checkpoint}")
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower()
        if mode != "delete":
            raise RuntimeError(f"Could not normalize SQLite backup journal mode: {mode}")
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {check}")
    finally:
        conn.close()


def _acquire_stopped_lock(state_dir: Path):
    """Production Repository owns this flock; fail if the service is running."""
    lock = (state_dir / "state.db.single-node.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("SyntheticForge is running; stop the application before backup or restore") from None
    return lock


def create_backup(state_dir: Path | str, output: Path | str) -> Path:
    root = Path(state_dir).resolve()
    db_path = root / "state.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"No state database found at {db_path}")
    destination = Path(output).resolve()
    if destination == db_path or root in destination.parents:
        raise ValueError("Backup archive must be outside the live state directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_stopped_lock(root)
    try:
        with tempfile.TemporaryDirectory(prefix="syntheticforge-backup-") as tmp:
            stage = Path(tmp)
            snapshot = stage / "state.db"
            source = sqlite3.connect(db_path, timeout=30)
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            _make_database_portable(snapshot)

            datasets = root / "datasets"
            if datasets.exists():
                if any(item.is_symlink() for item in datasets.rglob("*")):
                    raise ValueError("Dataset artifact directory contains a symlink; refusing an ambiguous backup")
                shutil.copytree(datasets, stage / "datasets", symlinks=False)
            payloads: list[dict[str, Any]] = []
            with sqlite3.connect(snapshot) as conn:
                for row in conn.execute("SELECT id,payload_json FROM datasets ORDER BY id"):
                    try:
                        payload = json.loads(row[1])
                        exports = payload.get("exports", [])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    for export in exports:
                        path = Path(export)
                        try:
                            relative = path.resolve().relative_to(root)
                        except (ValueError, OSError):
                            raise ValueError(f"Dataset {row[0]} has an artifact outside the state directory") from None
                        if relative.parts[:1] != ("datasets",) or not path.is_file():
                            raise ValueError(f"Dataset {row[0]} has an artifact missing from the state directory")
                        payloads.append({"dataset_id": row[0], "path": relative.as_posix()})

            # Only hash files that are explicitly written to the archive. SQLite
            # may create transient -wal/-shm files while the snapshot is read.
            archive_files = [snapshot]
            if (stage / "datasets").exists():
                archive_files.extend(p for p in sorted((stage / "datasets").rglob("*")) if p.is_file())
            file_hashes = {p.relative_to(stage).as_posix(): _sha256(p) for p in archive_files}
            manifest = {
                "format_version": FORMAT_VERSION,
                "state_directory": str(root),
                "application_sha": os.getenv("SYNTHETICFORGE_APPLICATION_SHA") or None,
                "config_references": list(CONFIG_REFERENCES),
                "included_artifact_references": payloads,
                "sha256": file_hashes,
                "environment_secret_values_included": False,
            }
            archive_tmp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            try:
                with zipfile.ZipFile(archive_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    zf.write(snapshot, "state.db")
                    if (stage / "datasets").exists():
                        for item in sorted((stage / "datasets").rglob("*")):
                            if item.is_file():
                                zf.write(item, item.relative_to(stage).as_posix())
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
                os.chmod(archive_tmp, 0o600)
                os.replace(archive_tmp, destination)
            finally:
                archive_tmp.unlink(missing_ok=True)
    finally:
        lock.close()
    return destination


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe archive member: {name}")
    if "\\" in name:
        raise ValueError(f"Unsafe archive member: {name}")
    return path


def restore_backup(archive: Path | str, destination: Path | str) -> Path:
    source = Path(archive).resolve()
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Restore destination must be absent or empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="syntheticforge-restore-", dir=target.parent) as tmp:
        stage = Path(tmp) / "restored"
        stage.mkdir(mode=0o700)
        with zipfile.ZipFile(source) as zf:
            names: dict[str, zipfile.ZipInfo] = {}
            for info in zf.infolist():
                safe = _safe_member(info.filename)
                if info.is_dir():
                    continue
                if info.filename in names:
                    raise ValueError(f"Duplicate archive member: {info.filename}")
                names[info.filename] = info
                out = stage.joinpath(*safe.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as inp, out.open("wb") as dst:
                    shutil.copyfileobj(inp, dst)
        manifest_path = stage / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != FORMAT_VERSION or manifest.get("environment_secret_values_included") is not False:
            raise ValueError("Unsupported or invalid SyntheticForge backup manifest")
        hashes = manifest.get("sha256")
        if not isinstance(hashes, dict):
            raise ValueError("Backup manifest has no integrity map")
        archive_payloads = set(names) - {"manifest.json"}
        if archive_payloads != set(hashes):
            raise ValueError("Backup members do not match the integrity manifest")
        for name, digest in hashes.items():
            safe = _safe_member(name)
            member = stage.joinpath(*safe.parts)
            if not member.is_file() or _sha256(member) != digest:
                raise ValueError(f"Backup integrity check failed for {name}")
        db_path = stage / "state.db"
        if not db_path.is_file():
            raise ValueError("Backup does not contain state.db")
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise ValueError(f"Restored database integrity check failed: {check}")
                old_root = Path(manifest["state_directory"]).resolve()
                for row in conn.execute("SELECT id,payload_json FROM datasets"):
                    payload = json.loads(row[1])
                    exports = []
                    changed = False
                    for export in payload.get("exports", []):
                        candidate = Path(export)
                        try:
                            relative = candidate.resolve().relative_to(old_root)
                        except ValueError:
                            exports.append(export)
                            continue
                        exports.append(str(target / relative))
                        changed = True
                    if changed:
                        payload["exports"] = exports
                        conn.execute("UPDATE datasets SET payload_json=? WHERE id=?", (json.dumps(payload), row[0]))
        finally:
            conn.close()
        _make_database_portable(db_path)
        manifest_path.unlink()
        if target.exists():
            moved: list[Path] = []
            try:
                for item in stage.iterdir():
                    final = target / item.name
                    os.replace(item, final)
                    moved.append(final)
            except Exception:
                for final in reversed(moved):
                    os.replace(final, stage / final.name)
                raise
        else:
            os.replace(stage, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up or restore SyntheticForge SQLite state")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="Create a consistent backup; application must be stopped")
    backup.add_argument("--state-dir", default=os.getenv("SYNTHETICFORGE_HOME", str(Path.home() / ".syntheticforge")))
    backup.add_argument("--output", required=True)
    restore = sub.add_parser("restore", help="Restore to an absent or empty state directory")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        result = create_backup(args.state_dir, args.output) if args.command == "backup" else restore_backup(args.archive, args.destination)
        print(result)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
