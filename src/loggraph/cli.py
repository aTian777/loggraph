from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from loggraph.indexer import Indexer
from loggraph.graph.store import save_index, load_index
from loggraph.logs.parser import parse_log_block, parse_log_text
from loggraph.matchers.locator import Locator
from loggraph.graph.render import render
from loggraph.evaluation.runner import evaluate
from loggraph.analyzer import analyze_log, compare_logs, compact_summary, default_index_path, write_analysis
from loggraph.profile import default_profile_path, load_project_profile, merge_manual_profiles, parse_simple_yaml, render_manual_profile, render_profile_suggestion
from loggraph.quality import audit_index, cleanup_profile, diagnose_project, doctor_project, lint_profile, load_cleanup_patch, refine_profile, render_audit_report, render_cleanup_report, render_doctor_report, render_profile_lint_report, sequence_from_log, suggest_app_identifiers


def cmd_index(args):
    idx = Indexer().build(args.src)
    save_index(idx, args.out)
    print(json.dumps({"functions": len(idx.functions), "calls": len(idx.calls), "log_sites": len(idx.log_sites), "out": args.out}, indent=2))


def cmd_init(args):
    out = Path(args.out) if args.out else default_index_path(args.project)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = Path(args.src) if args.src else Path(args.project)
    
    # Load existing index if incremental mode is enabled
    existing_index = None
    if not args.no_incremental and out.exists():
        try:
            existing_index = load_index(out)
        except Exception:
            pass  # If loading fails, start fresh
    
    def emit_progress(event: dict):
        if args.progress_jsonl:
            print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

    project_profile = load_project_profile(args.project)
    indexer = Indexer(max_workers=args.workers, incremental=not args.no_incremental, exclude_paths=project_profile.get("exclude_paths", []))
    idx = indexer.build(src, existing_index=existing_index, progress=emit_progress if args.progress_jsonl else None)
    if args.progress_jsonl:
        print(json.dumps({"phase": "write_cache", "path": str(out), "message": "Writing index cache"}, ensure_ascii=False), file=sys.stderr, flush=True)
    save_index(idx, out)
    event_profile = idx.metadata.get("event_profile", {})
    print(json.dumps({
        "project": str(Path(args.project).resolve()),
        "src": str(src.resolve()),
        "cache": str(out),
        "functions": len(idx.functions),
        "calls": len(idx.calls),
        "log_sites": len(idx.log_sites),
        "event_profile": {
            "learned_patterns": len(event_profile.get("learned_patterns", [])),
            "session_keys": len(event_profile.get("session_keys", [])),
            "states": len(event_profile.get("states", [])),
        },
        "incremental": not args.no_incremental,
        "workers": args.workers or "sequential",
    }, ensure_ascii=False, indent=2))


def _print_candidates(cands):
    print(json.dumps([asdict(c) for c in cands], indent=2))


def cmd_query(args):
    idx = load_index(args.index)
    entry = parse_log_block(args.log)
    _print_candidates(Locator(idx).locate(entry, top=args.top))


def cmd_locate(args):
    idx = load_index(args.index)
    text = Path(args.log_file).read_text(encoding="utf-8")
    locator = Locator(idx)
    results = []
    for entry in parse_log_text(text):
        results.append({"log": entry.raw, "candidates": [asdict(c) for c in locator.locate(entry, top=args.top)]})
    print(json.dumps(results, indent=2))


def cmd_analyze(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    out = Path(args.out) if args.out else Path(args.project) / ".loggraph" / (Path(args.log_file).stem + ".analysis.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    report = analyze_log(index_path, args.log_file, top=args.top, app_only=not args.all_lines, project=args.project, context=args.context, source_context=args.source_context, detail=args.detail, query=args.query or "")
    write_analysis(report, out)
    summary = compact_summary(report, max_matches=args.show_matches)
    summary["out"] = str(out)
    if args.format == "markdown":
        print(summary.get("report_markdown", ""))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_explain(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = analyze_log(index_path, args.log_file, top=args.top, app_only=not args.all_lines, project=args.project, context=args.context, source_context=args.source_context, detail="normal", query=args.query or "")
    payload = {
        "diagnosis": report.get("diagnosis", {}),
        "evidence_trace": report.get("evidence_trace", []),
        "profile_warnings": report.get("profile_warnings", []),
        "query_focus": report.get("query_focus", {}),
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("# LogGraph Explanation\n")
    diagnosis = payload["diagnosis"]
    if diagnosis.get("summary"):
        print(f"## Diagnosis\n- {diagnosis['summary']}")
    if diagnosis.get("findings"):
        print("\n## Findings")
        for item in diagnosis["findings"]:
            print(f"- {item}")
    if payload["evidence_trace"]:
        print("\n## Evidence trace")
        for idx, step in enumerate(payload["evidence_trace"], 1):
            suffix = f" — {step.get('detail')}" if step.get("detail") else ""
            print(f"{idx}. {step.get('label')}{suffix}")
            if step.get("source"):
                print(f"   - source: `{step['source']}`")
            if step.get("line"):
                print(f"   - log line: {step['line']}")
    if payload["profile_warnings"]:
        print("\n## Profile warnings")
        for warning in payload["profile_warnings"]:
            print(f"- {warning.get('message', '')}")
            if warning.get("suggestion"):
                print(f"  - suggestion: {warning['suggestion']}")


def cmd_profile_suggest(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    if args.from_log:
        report = refine_profile(index_path, args.from_log, project=args.project, all_lines=args.all_lines, query=args.query or "")
        text = report["patch_yaml"]
    else:
        idx = load_index(index_path)
        profile = dict(idx.metadata.get("event_profile", {}))
        profile.setdefault("app_identifiers", suggest_app_identifiers(args.project))
        text = render_profile_suggestion(profile)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")


def cmd_profile_init(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    profile_path = Path(args.out) if args.out else default_profile_path(args.project)
    if profile_path.exists() and not args.force:
        raise SystemExit(f"Profile already exists: {profile_path}. Use --force to overwrite.")
    idx = load_index(index_path)
    text = render_profile_suggestion(idx.metadata.get("event_profile", {}))
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(text, encoding="utf-8")
    print(json.dumps({"profile": str(profile_path), "written": True}, ensure_ascii=False, indent=2))


def cmd_profile_refine(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = refine_profile(index_path, args.log_file, project=args.project, all_lines=args.all_lines, query=args.query or "")
    if args.apply:
        apply_profile_patch(args.project, report["patch_yaml"], force=True)
        report["applied"] = True
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report["patch_yaml"], encoding="utf-8")
    if args.format == "yaml":
        print(report["patch_yaml"], end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_profile_apply(args):
    patch_text = Path(args.patch).read_text(encoding="utf-8")
    profile_path = apply_profile_patch(args.project, patch_text, force=args.force)
    print(json.dumps({"profile": str(profile_path), "applied": True}, ensure_ascii=False, indent=2))


def cmd_profile_sequence(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = sequence_from_log(index_path, args.log_file, project=args.project, name=args.name, all_lines=args.all_lines, query=args.query or "")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report["yaml"], encoding="utf-8")
    if args.format == "yaml":
        print(report["yaml"], end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_profile_lint(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = lint_profile(args.project, index_path, log_file=args.log_file, query=args.query or "", all_lines=args.all_lines, fix_suggest=args.fix_suggest)
    if args.format == "markdown":
        print(render_profile_lint_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and any(problem.get("severity") in {"error", "warning"} for problem in report.get("problems", [])):
        return 1


def cmd_profile_cleanup(args):
    patch = load_cleanup_patch(args.patch)
    report = cleanup_profile(args.project, patch, apply=args.apply)
    if args.format == "markdown":
        print(render_cleanup_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_audit(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = audit_index(index_path)
    report["report_markdown"] = render_audit_report(report)
    if args.format == "markdown":
        print(report["report_markdown"])
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_doctor(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = doctor_project(args.project, index_path, log_file=args.log_file, query=args.query or "", all_lines=args.all_lines)
    if args.format == "markdown":
        print(render_doctor_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_diagnose(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = diagnose_project(args.project, index_path, args.log_file, query=args.query or "", all_lines=args.all_lines)
    if args.save_artifacts:
        report["artifacts"] = diagnosis_artifact_paths(args.project, args.log_file)
        save_diagnosis_artifacts(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        if args.format == "markdown":
            Path(args.out).write_text(report["report_markdown"], encoding="utf-8")
        else:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "markdown":
        print(report["report_markdown"])
        if report.get("artifacts"):
            print("\n## Saved artifacts")
            for key, value in report["artifacts"].items():
                print(f"- {key}: `{value}`")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def diagnosis_artifact_paths(project: str, log_file: str) -> dict[str, str]:
    out_dir = Path(project) / ".loggraph" / "reports"
    stem = Path(log_file).stem
    return {
        "markdown": str(out_dir / f"{stem}.diagnosis.md"),
        "json": str(out_dir / f"{stem}.diagnosis.json"),
        "cleanup": str(out_dir / f"{stem}.cleanup.json"),
    }


def save_diagnosis_artifacts(report: dict) -> None:
    paths = {key: Path(value) for key, value in report["artifacts"].items()}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(report["report_markdown"], encoding="utf-8")
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cleanup_patch = report.get("profile_lint", {}).get("cleanup_patch") or {}
    paths["cleanup"].write_text(json.dumps({"cleanup_patch": cleanup_patch}, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_compare(args):
    index_path = Path(args.index) if args.index else default_index_path(args.project)
    report = compare_logs(index_path, args.baseline, args.target, project=args.project, top=args.top, app_only=not args.all_lines, context=args.context, detail=args.detail, query=args.query or "")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "markdown":
        print(report["report_markdown"])
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.fail_on_regression and (report.get("duration_anomalies") or report.get("hypotheses") or report.get("missing_in_target")):
        return 1


def apply_profile_patch(project: str, patch_text: str, *, force: bool) -> Path:
    profile_path = default_profile_path(project)
    base = load_project_profile(project) if profile_path.exists() else {}
    patch = parse_simple_yaml(patch_text)
    merged = merge_manual_profiles(base, patch)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if profile_path.exists() and not force:
        raise SystemExit(f"Profile already exists: {profile_path}. Use --force to overwrite/apply.")
    profile_path.write_text(render_manual_profile(merged), encoding="utf-8")
    return profile_path


def cmd_render(args):
    idx = load_index(args.index)
    if args.log:
        cands = Locator(idx).locate(parse_log_block(args.log), top=args.top)
    else:
        ids = set(args.function_id or [])
        cands = []
        for fid in ids:
            fn = idx.functions[fid]
            from loggraph.models import Candidate
            cands.append(Candidate(f"manual:{fid}", 1.0, fid, fn.qualname, fn.file, fn.start_line, ["manual render selection"]))
    out = render(idx, cands, args.out)
    print(str(out))


def cmd_evaluate(args):
    res = evaluate(args.src, args.corpus, top=args.top, tolerance=args.tolerance)
    payload = {"total": res.total, "top1": res.top1, "top3": res.top3, "accuracy": res.accuracy, "failures": res.failures[: args.show_failures]}
    print(json.dumps(payload, indent=2))
    if res.accuracy < args.min_accuracy:
        return 1
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="loggraph")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index")
    s.add_argument("src")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_index)
    s = sub.add_parser("init")
    s.add_argument("project")
    s.add_argument("--src", help="Source directory to index. Defaults to project root.")
    s.add_argument("--out", help="Cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--workers", type=int, help="Number of parallel workers for indexing. Default: sequential.")
    s.add_argument("--no-incremental", action="store_true", help="Disable incremental indexing and rebuild from scratch.")
    s.add_argument("--progress-jsonl", action="store_true", help="Emit init progress events as JSON Lines on stderr.")
    s.set_defaults(func=cmd_init)
    s = sub.add_parser("query")
    s.add_argument("index")
    s.add_argument("--log", required=True)
    s.add_argument("--top", type=int, default=3)
    s.set_defaults(func=cmd_query)
    s = sub.add_parser("locate")
    s.add_argument("index")
    s.add_argument("--log-file", required=True)
    s.add_argument("--top", type=int, default=3)
    s.set_defaults(func=cmd_locate)
    s = sub.add_parser("analyze")
    s.add_argument("project")
    s.add_argument("--log-file", required=True)
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--out", help="Analysis report path. Defaults to <project>/.loggraph/<log-stem>.analysis.json.")
    s.add_argument("--top", type=int, default=3)
    s.add_argument("--show-matches", type=int, default=10)
    s.add_argument("--all-lines", action="store_true", help="Analyze all log lines instead of app-tag lines only.")
    s.add_argument("--format", choices=["json", "markdown"], default="json", help="Output compact JSON or a human-readable markdown report.")
    s.add_argument("--context", type=int, default=0, help="Include N log lines before/after suspicious events and source matches.")
    s.add_argument("--source-context", type=int, default=3, help="Include N source lines around each candidate.")
    s.add_argument("--detail", choices=["brief", "normal", "full"], default="normal", help="Report detail level.")
    s.add_argument("--query", help="Focus analysis on log entries matching these natural-language terms.")
    s.set_defaults(func=cmd_analyze)
    s = sub.add_parser("explain")
    s.add_argument("project")
    s.add_argument("--log-file", required=True)
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--top", type=int, default=3)
    s.add_argument("--all-lines", action="store_true", help="Analyze all log lines instead of app-tag lines only.")
    s.add_argument("--context", type=int, default=0)
    s.add_argument("--source-context", type=int, default=3)
    s.add_argument("--query", help="Focus explanation on log entries matching these natural-language terms.")
    s.add_argument("--format", choices=["json", "markdown"], default="markdown")
    s.set_defaults(func=cmd_explain)
    s = sub.add_parser("profile")
    profile_sub = s.add_subparsers(dest="profile_cmd", required=True)
    ps = profile_sub.add_parser("suggest")
    ps.add_argument("project")
    ps.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    ps.add_argument("--out", help="Write suggested profile to this path.")
    ps.add_argument("--from-log", help="Also use a real log file to suggest profile rules.")
    ps.add_argument("--query", help="Focus log-based suggestions on these terms.")
    ps.add_argument("--all-lines", action="store_true")
    ps.set_defaults(func=cmd_profile_suggest)
    pi = profile_sub.add_parser("init")
    pi.add_argument("project")
    pi.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    pi.add_argument("--out", help="Profile path. Defaults to <project>/.loggraph/profile.yaml.")
    pi.add_argument("--force", action="store_true", help="Overwrite an existing profile.")
    pi.set_defaults(func=cmd_profile_init)
    pr = profile_sub.add_parser("refine")
    pr.add_argument("project")
    pr.add_argument("--log-file", required=True)
    pr.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    pr.add_argument("--all-lines", action="store_true")
    pr.add_argument("--query", help="Focus refinement on these terms.")
    pr.add_argument("--format", choices=["json", "yaml"], default="yaml")
    pr.add_argument("--out", help="Write suggested YAML patch to this path.")
    pr.add_argument("--apply", action="store_true", help="Apply the suggested patch to <project>/.loggraph/profile.yaml.")
    pr.set_defaults(func=cmd_profile_refine)
    pl = profile_sub.add_parser("lint")
    pl.add_argument("project")
    pl.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    pl.add_argument("--log-file", help="Validate profile rules against a real log file.")
    pl.add_argument("--query", help="Focus log-based lint checks on these terms.")
    pl.add_argument("--all-lines", action="store_true")
    pl.add_argument("--fix-suggest", action="store_true", help="Include structured cleanup suggestions. Does not modify profile files.")
    pl.add_argument("--strict", action="store_true", help="Exit 1 when warnings or errors are found; info-level findings do not fail.")
    pl.add_argument("--format", choices=["json", "markdown"], default="markdown")
    pl.set_defaults(func=cmd_profile_lint)
    pc = profile_sub.add_parser("cleanup")
    pc.add_argument("project")
    pc.add_argument("--patch", required=True, help="JSON cleanup patch or full profile lint JSON containing cleanup_patch.")
    mode = pc.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview cleanup changes without writing. Default.")
    mode.add_argument("--apply", action="store_true", help="Apply safe cleanup removals. Review-only items are not modified.")
    pc.add_argument("--format", choices=["json", "markdown"], default="markdown")
    pc.set_defaults(func=cmd_profile_cleanup)
    pa = profile_sub.add_parser("apply")
    pa.add_argument("project")
    pa.add_argument("--patch", required=True, help="YAML patch file to merge into .loggraph/profile.yaml.")
    pa.add_argument("--force", action="store_true", help="Allow updating an existing profile.")
    pa.set_defaults(func=cmd_profile_apply)
    pq = profile_sub.add_parser("sequence")
    pq.add_argument("project")
    pq.add_argument("--from-log", dest="log_file", required=True)
    pq.add_argument("--name", default="success")
    pq.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    pq.add_argument("--all-lines", action="store_true")
    pq.add_argument("--query", help="Focus sequence extraction on these terms.")
    pq.add_argument("--format", choices=["json", "yaml"], default="yaml")
    pq.add_argument("--out", help="Write expected sequence YAML to this path.")
    pq.set_defaults(func=cmd_profile_sequence)
    s = sub.add_parser("audit")
    s.add_argument("project")
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--format", choices=["json", "markdown"], default="markdown")
    s.set_defaults(func=cmd_audit)
    s = sub.add_parser("doctor")
    s.add_argument("project")
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--log-file", help="Optional log file for log-aware profile health checks.")
    s.add_argument("--query", help="Focus log-aware doctor checks on these terms.")
    s.add_argument("--all-lines", action="store_true", help="Analyze all log lines for log-aware checks.")
    s.add_argument("--format", choices=["json", "markdown"], default="markdown")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("diagnose")
    s.add_argument("project")
    s.add_argument("--log-file", required=True)
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--query", help="Focus diagnosis on these terms.")
    s.add_argument("--all-lines", action="store_true")
    s.add_argument("--format", choices=["json", "markdown"], default="markdown")
    s.add_argument("--out", help="Write unified diagnosis report to this path.")
    s.add_argument("--save-artifacts", action="store_true", help="Write .loggraph/reports/<log>.diagnosis.md, .diagnosis.json, and .cleanup.json.")
    s.set_defaults(func=cmd_diagnose)
    s = sub.add_parser("compare")
    s.add_argument("project")
    s.add_argument("--baseline", required=True, help="Successful/baseline log file.")
    s.add_argument("--target", required=True, help="Failed/target log file.")
    s.add_argument("--index", help="Index cache path. Defaults to <project>/.loggraph/index.json.")
    s.add_argument("--top", type=int, default=3)
    s.add_argument("--all-lines", action="store_true", help="Analyze all log lines instead of app-tag lines only.")
    s.add_argument("--context", type=int, default=0)
    s.add_argument("--detail", choices=["brief", "normal", "full"], default="normal")
    s.add_argument("--format", choices=["json", "markdown"], default="markdown")
    s.add_argument("--out", help="Write JSON compare report to this path.")
    s.add_argument("--fail-on-regression", action="store_true", help="Exit with status 1 when regressions/hypotheses are detected.")
    s.add_argument("--query", help="Focus comparison on log entries matching these natural-language terms.")
    s.set_defaults(func=cmd_compare)
    s = sub.add_parser("render")
    s.add_argument("index")
    s.add_argument("--log")
    s.add_argument("--function-id", action="append")
    s.add_argument("--top", type=int, default=3)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_render)
    s = sub.add_parser("evaluate")
    s.add_argument("--src", required=True)
    s.add_argument("--corpus", required=True)
    s.add_argument("--top", type=int, default=3)
    s.add_argument("--tolerance", type=int, default=3)
    s.add_argument("--min-accuracy", type=float, default=0.90)
    s.add_argument("--show-failures", type=int, default=10)
    s.set_defaults(func=cmd_evaluate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    rc = args.func(args)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
