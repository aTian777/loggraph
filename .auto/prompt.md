# Autoresearch: Improve LogGraph Retrieval Speed by 50%

## Objective
Optimize LogGraph log analysis/retrieval speed without reducing correctness. The target workload is analyzing a real Android application log against a cached LogGraph index, CodeGraph-style: `init` builds `.loggraph/index.json`, then `analyze` reuses the cache.

## Metrics
- **Primary**: total_ms (ms, lower is better) — wall-clock time for `analyze_log()` on the real smartroom log using the cached index.
- **Secondary**: matched_lines, bottle_count — correctness/tradeoff monitors. matched_lines should remain nonzero and bottle_count should remain 4 for the target log.

## How to Run
`./.auto/measure.sh` — emits `METRIC total_ms=...`, `METRIC matched_lines=...`, and `METRIC bottle_count=...`.

## Files in Scope
- `src/loggraph/analyzer.py` — high-level log analysis loop and domain findings.
- `src/loggraph/matchers/locator.py` — candidate scoring and log-site matching; likely main bottleneck.
- `src/loggraph/logs/templates.py` — template regex/similarity helpers.
- `src/loggraph/models.py` — data model additions only if needed for cached/precomputed matching.
- `src/loggraph/graph/store.py` — index load/save only if needed for cache compatibility.
- Tests under `tests/` and docs if behavior changes.

## Off Limits
- Do not change benchmark inputs or hard-code answers for the smartroom log.
- Do not reduce analysis coverage by skipping legitimate app log lines just to improve speed.
- Do not remove correctness checks or fake METRIC output.

## Constraints
- No new runtime dependencies unless absolutely necessary.
- Keep CLI behavior compatible: `loggraph init`, `loggraph analyze`, `loggraph query` must still work.
- `PYTHONPATH=src python3 -m unittest discover -s tests -p '*unittest.py' -v` must pass.
- `PYTHONPATH=src python3 -m loggraph.cli evaluate --src fixtures/python_projects/service_app --corpus fixtures/labeled_logs/corpus.jsonl --top 3 --min-accuracy 0.90` must pass.
- The real-log benchmark must continue reporting `bottle_count=4`.

## What's Been Tried
- Baseline: 148,153ms - initial implementation scans all log sites for each app log line and calls template/fuzzy helpers repeatedly.
- Pre-compile regex: 101,033ms (32% faster) - pre-compile regex at Locator init, skip fuzzy for short messages.
- Keyword filtering: 25,007ms (83% faster) - extract keywords from template and message, skip log sites with no shared keywords.
- Hybrid similarity: 11,333ms (92% faster) - fast length check, word overlap for high similarity, SequenceMatcher fallback.
- Word set lookup: 10,081ms (93% faster) - extract words from haystack into set, use set membership instead of substring search.
- Jaccard similarity: checks_failed - broke test correctness (gave 0.6 instead of >0.7).
