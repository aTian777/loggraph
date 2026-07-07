#!/usr/bin/env bash
set -euo pipefail
cd /home/xty/loggraph
export PYTHONPATH=src
python3 -m unittest discover -s tests -p '*unittest.py' -v >/tmp/loggraph-unittest.out
python3 -m loggraph.cli evaluate --src fixtures/python_projects/service_app --corpus fixtures/labeled_logs/corpus.jsonl --top 3 --min-accuracy 0.90 >/tmp/loggraph-eval.out
./.auto/measure.sh | tee /tmp/loggraph-check-measure.out
if ! grep -q 'METRIC bottle_count=4' /tmp/loggraph-check-measure.out; then
  echo 'bottle_count correctness check failed'
  cat /tmp/loggraph-check-measure.out
  exit 1
fi
