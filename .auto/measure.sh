#!/usr/bin/env bash
set -euo pipefail
cd /home/xty/loggraph
export PYTHONPATH=src
python3 - <<'PY'
from pathlib import Path
from time import perf_counter
from loggraph.analyzer import analyze_log
project = Path('/home/xty/AndroidProject/smartroom-and-return')
index = project / '.loggraph/index.json'
log = project / '20260706171830-21600232.log'
if not index.exists():
    raise SystemExit(f'missing index: {index}')
start = perf_counter()
report = analyze_log(index, log, top=3, app_only=True)
elapsed_ms = (perf_counter() - start) * 1000.0
print(f"METRIC total_ms={elapsed_ms:.3f}")
print(f"METRIC matched_lines={report['matched_log_lines']}")
print(f"METRIC bottle_count={report['domain_findings']['bottle_count_from_rty_sum']}")
PY
