#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python -m app.cli generate --input examples/hospitality_full.sql --format ddl --database-type postgresql --database-name hospitality_demo --schema demo --rows 100 --seed 2026 --scenario "12% cancellations" --output syntheticforge-hospitality-demo.zip
