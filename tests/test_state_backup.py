from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.models import ProjectCreateRequest
from app.persistence import Repository
from app.state_backup import create_backup, restore_backup


def populated_state(root: Path) -> tuple[str, str]:
    repo = Repository(root / "state.db")
    project = repo.create_project(ProjectCreateRequest(name="backup fixture", settings={"api_token": "fixture-secret"}))
    dataset = repo.create_dataset(
        project_id=project["id"], seed=7, schema={"tables": []}, rules={}, row_counts={}, validation={"passed": True}
    )
    artifact = repo.save_dataset_artifact(dataset.id, b"synthetic artifact bytes", "fixture.zip")
    return dataset.id, artifact


def test_backup_restores_database_artifacts_and_relocated_export_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    dataset_id, artifact = populated_state(source)
    archive = tmp_path / "backup.zip"
    monkeypatch.setenv("SYNTHETICFORGE_API_TOKEN", "do-not-copy-this-value")

    create_backup(source, archive)
    assert archive.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        assert "state.db" in names
        assert f"datasets/{dataset_id}/fixture.zip" in names
        assert "do-not-copy-this-value" not in zf.read("state.db").decode("latin-1")
        assert manifest["environment_secret_values_included"] is False
        assert "SYNTHETICFORGE_ADMIN_PASSWORD_HASH" in manifest["config_references"]

    restored = tmp_path / "restored"
    restored.mkdir()  # Also models an empty mounted volume root.
    restore_backup(archive, restored)
    repo = Repository(restored / "state.db")
    dataset = repo.get_dataset(dataset_id)
    assert len(dataset.exports) == 1
    relocated = Path(dataset.exports[0])
    assert relocated == restored.resolve() / "datasets" / dataset_id / "fixture.zip"
    assert relocated.read_bytes() == b"synthetic artifact bytes"


def test_backup_refuses_running_production_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "state"
    monkeypatch.setenv("SYNTHETICFORGE_ENV", "production")
    monkeypatch.setenv("SYNTHETICFORGE_HOME", str(root))
    repo = Repository(root / "state.db")
    try:
        with pytest.raises(RuntimeError, match="stop the application"):
            create_backup(root, tmp_path / "backup.zip")
    finally:
        repo._single_node_lock.close()


def test_restore_rejects_corrupt_or_nonempty_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    populated_state(source)
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep").write_text("untouched")
    with pytest.raises(FileExistsError):
        restore_backup(archive, target)
    assert (target / "keep").read_text() == "untouched"

    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(corrupt, "w") as output:
        for name in original.namelist():
            payload = original.read(name)
            if name == "state.db":
                payload += b"corruption"
            output.writestr(name, payload)
    with pytest.raises(ValueError, match="integrity check failed"):
        restore_backup(corrupt, tmp_path / "bad-restore")
    assert not (tmp_path / "bad-restore").exists()


def test_restore_rejects_path_traversal_archive(tmp_path: Path) -> None:
    archive = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside", b"bad")
    with pytest.raises(ValueError, match="Unsafe archive member"):
        restore_backup(archive, tmp_path / "restore")
    assert not (tmp_path / "outside").exists()


def test_module_cli_backup_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "cli-source"
    populated_state(source)
    archive = tmp_path / "cli-backup.zip"
    restored = tmp_path / "cli-restored"
    env = os.environ.copy()
    backup = subprocess.run(
        [sys.executable, "-m", "app.state_backup", "backup", "--state-dir", str(source), "--output", str(archive)],
        check=False, capture_output=True, text=True, env=env,
    )
    assert backup.returncode == 0, backup.stderr
    result = subprocess.run(
        [sys.executable, "-m", "app.state_backup", "restore", "--archive", str(archive), "--destination", str(restored)],
        check=False, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (restored / "state.db").is_file()
