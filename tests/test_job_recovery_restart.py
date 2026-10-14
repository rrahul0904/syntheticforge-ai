from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from app.jobs import JobManager
from app.models import ColumnSpec, JobGenerateRequest, ProjectCreateRequest, SystemGenerateRequest, TableSpec
from app.persistence import Repository


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_app(home: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "SYNTHETICFORGE_ENV": "development",
        "SYNTHETICFORGE_HOME": str(home),
        "PYTHONPATH": str(Path.cwd()),
    })
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Application exited before readiness (exit={process.returncode})")
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("Application did not become healthy within 15 seconds")


def _stop_app(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _generation_payload() -> JobGenerateRequest:
    request = SystemGenerateRequest(
        database_type="sqlite",
        database_name="restart-test",
        schema_name="main",
        default_row_count=2,
        seed=17,
        tables=[TableSpec(
            name="records",
            schema_name="main",
            row_count=2,
            columns=[ColumnSpec(name="id", data_type="integer", primary_key=True)],
        )],
    )
    return JobGenerateRequest(request=request)


def test_job_recovery_policy_survives_a_real_application_process_restart(tmp_path: Path):
    home = tmp_path / "state"
    home.mkdir()
    first_port, second_port = _free_port(), _free_port()
    first = _start_app(home, first_port)
    second = None
    try:
        _wait_ready(first, first_port)
        repo = Repository(home / "state.db")
        manager = JobManager(repo)
        project = repo.create_project(ProjectCreateRequest(name="restart recovery"))
        payload = _generation_payload().model_dump(mode="json")

        queued = manager.create(payload=payload)
        active = manager.create(project["id"], payload)
        assert repo.claim_job(active.id)

        completed = manager.create(payload=payload)
        manager.run(completed.id, lambda update, cancelled: "finished")
        failed = manager.create(payload=payload)
        with pytest.raises(RuntimeError, match="expected failure"):
            manager.run(failed.id, lambda update, cancelled: (_ for _ in ()).throw(RuntimeError("expected failure")))
        cancelled = manager.create(payload=payload)
        manager.cancel(cancelled.id)

        corrupt = manager.create(payload={"incomplete": True})
        with repo.connect() as connection:
            connection.execute("UPDATE jobs SET payload_json=? WHERE id=?", ["{broken-json", corrupt.id])

        existing = repo.create_dataset(
            project_id=project["id"], seed=1, schema=[], rules=[], row_counts={},
            validation={"passed": True},
        )
        artifact_path = Path(repo.save_dataset_artifact(existing.id, b"pre-restart-artifact", "existing.zip"))
        artifact_before = artifact_path.read_bytes()

        target_path = tmp_path / "target.sqlite"
        with sqlite3.connect(target_path) as target:
            target.execute("CREATE TABLE writes (id INTEGER PRIMARY KEY)")
            target.execute("INSERT INTO writes VALUES (1)")
        repo.connector_receipt("restart-test", "target.load", "sqlite", "approved", metadata={"rows": 1})

        _stop_app(first)
        first = None

        # This is a separate operating-system process using the persisted app home.
        second = _start_app(home, second_port)
        _wait_ready(second, second_port)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and repo.get_job(queued.id).state not in {"completed", "failed"}:
            time.sleep(0.1)

        assert repo.get_job(queued.id).state == "completed"  # safe queued work resumed
        assert repo.get_job(active.id).state == "failed"  # uncertain active work is retryable, never replayed
        assert "application restart" in (repo.get_job(active.id).error or "")
        assert repo.get_job(completed.id).state == "completed"  # terminal work was not reprocessed
        assert repo.get_job(failed.id).state == "failed"  # retry remains explicit and creates a new job
        assert repo.get_job(cancelled.id).state == "cancelled"
        assert repo.get_job(corrupt.id).state == "failed"
        assert "corrupt or incomplete state" in (repo.get_job(corrupt.id).error or "")

        assert [item.id for item in repo.list_datasets(project["id"])] == [existing.id]
        assert artifact_path.read_bytes() == artifact_before
        with sqlite3.connect(target_path) as target:
            assert target.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 1
        assert len(repo.list_connector_receipts(q="target.load")) == 1
        events = [event["event"] for event in repo.audit_events(limit=100)]
        assert "job.recovery_queued" in events
        assert "job.recovery_interrupted" in events
        assert "job.recovery_rejected" in events
    finally:
        if first is not None and first.poll() is None:
            _stop_app(first)
        if second is not None and second.poll() is None:
            _stop_app(second)
