from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import ColumnSpec, GenerateRequest
from app.streaming import stream_csv, stream_ndjson, stream_sql

MAX_ROWS = 10_000_000
MAX_BATCH_SIZE = 100_000
DEFAULT_ROWS = 1_000_000  # Preserve the historical workload; use --rows for bounded runs.
STREAMERS: dict[str, tuple[str, Callable]] = {
    "ndjson": ("jsonl", stream_ndjson),
    "jsonl": ("jsonl", stream_ndjson),
    "csv": ("csv", stream_csv),
    "sql": ("sql", stream_sql),
}


def _bounded_int(label: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            result = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
        if not minimum <= result <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        return result

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark bounded, batched synthetic-data streaming")
    parser.add_argument("--rows", type=_bounded_int("rows", 1, MAX_ROWS), default=DEFAULT_ROWS,
                        help=f"rows to generate (1..{MAX_ROWS}; default: {DEFAULT_ROWS})")
    parser.add_argument("--format", choices=tuple(STREAMERS), default="ndjson",
                        help="streaming output format (jsonl is an alias for ndjson)")
    parser.add_argument("--seed", type=_bounded_int("seed", 0, 2_147_483_647), default=2026)
    parser.add_argument("--batch-size", type=_bounded_int("batch size", 1, MAX_BATCH_SIZE), default=10_000,
                        help=f"rows held per write batch (1..{MAX_BATCH_SIZE})")
    return parser


def peak_rss_bytes(raw_value: int | float, system: str | None = None) -> int:
    """Convert getrusage's platform-specific ru_maxrss to bytes."""
    current_system = system or sys.platform
    if current_system == "darwin":
        return int(raw_value)  # macOS reports bytes.
    if current_system.startswith("linux"):
        return int(raw_value * 1024)  # Linux reports KiB.
    raise RuntimeError(f"Peak RSS units are not defined for platform {current_system!r}")


def _source_metadata() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip())
    return sha, dirty


def run_benchmark(args: argparse.Namespace) -> dict[str, object]:
    output_format, stream = STREAMERS[args.format]
    request = GenerateRequest(
        database_type="postgresql", database_name="bench", schema_name="public",
        table_name="events", row_count=args.rows, seed=args.seed, ai_inference=False, columns=[],
    )
    columns = [
        ColumnSpec(name="event_id", data_type="bigint", semantic_type="id", primary_key=True),
        ColumnSpec(name="event_type", data_type="varchar", semantic_type="choice",
                   choices=["created", "updated", "processed", "completed"], nullable=False),
        ColumnSpec(name="score", data_type="integer", semantic_type="integer",
                   min_value=1, max_value=100, nullable=False),
    ]
    extension, _ = STREAMERS[args.format]
    with tempfile.TemporaryDirectory(prefix="syntheticforge-benchmark-") as tmp:
        output = Path(tmp) / f"benchmark.{extension}"
        started = time.perf_counter()
        count = stream(request, columns, output, batch_size=args.batch_size)
        elapsed = time.perf_counter() - started
        rss = peak_rss_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        source_sha, dirty = _source_metadata()
        return {
            "source_sha": source_sha,
            "working_tree_dirty": dirty,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "format": output_format,
            "rows": count,
            "seed": args.seed,
            "batch_size": args.batch_size,
            "elapsed_seconds": round(elapsed, 6),
            "rows_per_second": round(count / elapsed, 2) if elapsed else None,
            "peak_rss_bytes": rss,
            "peak_rss_mib": round(rss / (1024 * 1024), 2),
            "output_bytes": output.stat().st_size,
        }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(run_benchmark(args), indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
