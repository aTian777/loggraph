# LogGraph

LogGraph is a CodeGraph-inspired CLI that indexes Python, Kotlin, Java, TypeScript, Go, C, and C++ source code, extracts logger emission sites, parses logs, and ranks likely source locations. It also renders focused Graphviz subgraphs and verifies locator accuracy against a labeled corpus.

## Install

### As a Pi package from GitHub

```bash
pi install git:github.com/aTian777/loggraph@main
```

Pi loads the extension from `extensions/loggraph`. The extension runs `loggraph-shim.js`, which creates or repairs a local virtual environment at `~/.loggraph/venv` and installs this package in editable mode. If Python `venv` support is unavailable, install `uv` and the shim will use it as a fallback.

Update later with:

```bash
pi update --extensions
```

### Local development

```bash
cd loggraph
python3 -m pip install -e .
```

No runtime dependency is required. `pytest` is needed for tests. Graphviz `dot` is optional; without it LogGraph still writes `.dot` files.

## Commands

### Build an index

```bash
loggraph index fixtures/python_projects/service_app --out /tmp/loggraph-index.json
```

The index contains functions, methods, call edges, log sites, source paths, line numbers, and graph metadata.

### Query one log

```bash
loggraph query /tmp/loggraph-index.json --log "ERROR [orders] Failed to update order 42" --top 3
```

Each candidate includes score, source file, line, function, matched log site, callers, callees, and ranking reasons.

### Locate logs from a file

```bash
loggraph locate /tmp/loggraph-index.json --log-file app.log --top 3
```

### Analyze a log as a runtime report

```bash
loggraph analyze . --log-file app.log --format markdown --detail normal --context 3 --source-context 3 --all-lines
```

The report includes runtime events, session timelines, duration observations, rule-based hypotheses, diagnosis, evidence trace, source excerpts for likely code locations, missing expected events from `.loggraph/profile.yaml`, profile warnings, context windows around suspicious lines, and likely source areas. Use `--detail brief|normal|full` to control report verbosity.

### Explain a focused log question

```bash
loggraph explain . --log-file app.log --query "pcb await"
```

This prints a concise diagnosis, evidence trace, and profile warnings without the full analysis report.

### Compare a successful log with a failed log

```bash
loggraph compare . --baseline success.log --target failed.log --all-lines
loggraph compare . --baseline success.log --target failed.log --fail-on-regression
```

This highlights shared events, session-aligned differences, events missing from the failed target, extra target events, duration anomalies, rule-based hypotheses, and missing expected sequence items.

### Audit logging quality

```bash
loggraph audit .
```

This scores indexed source log sites for session keys, duration fields, state markers, error details, and generic templates. Use it to improve code logging before the next incident.

### Initialize, refine, or suggest a project profile

```bash
loggraph profile suggest .
loggraph profile init .
loggraph profile refine . --log-file failed.log --all-lines
loggraph profile refine . --log-file failed.log --all-lines --apply
loggraph profile apply . --patch patch.yaml --force
loggraph profile sequence . --from-log success.log --name delivery_success --all-lines
```

Profiles live at `.loggraph/profile.yaml` and let each project define its own session keys, event patterns, and expected sequences:

```yaml
session_keys:
  - deliveryId

events:
  await_pcb:
    type: await_pcb
    patterns:
      - AwaitPcb
  pcb_result:
    type: pcb_result
    patterns:
      - callback received

expected_sequences:
  delivery_success:
    - await_pcb
    - pcb_result
```

### Pi slash command shortcuts

Inside Pi, `/loggraph` supports common workflows:

```text
/loggraph init
/loggraph explain app.log pcb await
/loggraph app.log pcb await
/loggraph app.log 为什么失败
/loggraph compare success.log failed.log
/loggraph audit
/loggraph profile init
/loggraph profile refine failed.log pcb await
/loggraph profile suggest failed.log pcb await
/loggraph profile sequence success.log delivery
```

### Render a focused subgraph

```bash
loggraph render /tmp/loggraph-index.json --log "ERROR [users] User 0 not found" --out /tmp/loggraph.svg
```

If Graphviz is unavailable, LogGraph writes `/tmp/loggraph.dot`.

### Evaluate accuracy

```bash
loggraph evaluate \
  --src fixtures/python_projects/service_app \
  --corpus fixtures/labeled_logs/corpus.jsonl \
  --top 3 \
  --min-accuracy 0.90
```

The default completion gate is top-3 accuracy >= 90% where the true file and function must match and the predicted source line must be within tolerance.

## Supported evidence

- Plain log lines
- JSON log lines with common fields such as `message`, `level`, `logger`, `function`, `pathname`, and `lineno`
- Python traceback blocks
- Logger templates using `%s`, `%d`, `{}`, `{name}`, `.format(...)`, simple f-strings, and common C/C++ printf-style formats
- Fuzzy matching for partially changed messages

## Architecture

```text
source tree -> multi-language parsers -> code index(functions, calls, log sites)
logs        -> log parser          -> structured evidence
index+logs  -> locator/ranker      -> candidate source locations
candidate   -> Graphviz renderer   -> focused subgraph
corpus      -> evaluator           -> measured accuracy
```

## Known limitations

Python dynamic dispatch, monkey patching, generated code, and source/log version mismatch can reduce call-graph precision. LogGraph therefore treats logger-site and traceback evidence as primary signals and uses the call graph mainly for context and ranking tie-breaks.

## Validation used for this package

```bash
cd loggraph
PYTHONPATH=src python3 -m unittest discover -s tests -p '*unittest.py' -v
PYTHONPATH=src python3 -m loggraph.cli evaluate --src fixtures/python_projects/service_app --corpus fixtures/labeled_logs/corpus.jsonl --top 3 --min-accuracy 0.90
```
