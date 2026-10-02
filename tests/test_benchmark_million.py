from __future__ import annotations

import subprocess

import pytest

from scripts.benchmark_million import build_parser, peak_rss_bytes, run_benchmark


def test_benchmark_options_are_bounded_and_reported() -> None:
    args = build_parser().parse_args(["--rows", "25", "--format", "csv", "--seed", "17", "--batch-size", "7"])
    report = run_benchmark(args)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert report["source_sha"] == sha
    assert report["format"] == "csv"
    assert report["rows"] == 25
    assert report["seed"] == 17
    assert report["batch_size"] == 7
    assert report["output_bytes"] > 0
    assert report["peak_rss_bytes"] > 0
    assert report["python_version"]
    assert report["platform"]


@pytest.mark.parametrize("value,system,expected", [(4_194_304, "darwin", 4_194_304), (4096, "linux", 4_194_304)])
def test_peak_rss_conversion(value: int, system: str, expected: int) -> None:
    assert peak_rss_bytes(value, system) == expected


@pytest.mark.parametrize("option,value", [("--rows", "0"), ("--rows", "10000001"), ("--batch-size", "0"), ("--seed", "2147483648")])
def test_benchmark_rejects_out_of_bounds_options(option: str, value: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([option, value])
