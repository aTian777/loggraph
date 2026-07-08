import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from loggraph.cli import main as cli_main
from loggraph.indexer import Indexer
from loggraph.logs.parser import parse_log_block, parse_log_text
from loggraph.logs.templates import template_matches, similarity, template_to_regex
from loggraph.models import LogSite
from loggraph.matchers.locator import Locator
from loggraph.evaluation.runner import evaluate
from loggraph.graph.render import render_dot

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "fixtures/python_projects/service_app"
CORPUS = ROOT / "fixtures/labeled_logs/corpus.jsonl"


class LogGraphCoreTests(unittest.TestCase):
    def test_indexer_extracts_functions_calls_and_logs(self):
        idx = Indexer().build(SRC)
        self.assertGreaterEqual(len(idx.functions), 10)
        self.assertGreaterEqual(len(idx.log_sites), 10)
        self.assertTrue(any(s.template == "Failed to update order %s" for s in idx.log_sites.values()))
        self.assertTrue(any(fn.qualname == "PaymentService.charge" for fn in idx.functions.values()))

    def test_template_matching(self):
        self.assertTrue(template_matches("Failed to update order %s", "Failed to update order 42"))
        self.assertTrue(template_matches("Database unavailable for order {}", "Database unavailable for order 500"))
        self.assertGreater(similarity("Insufficient stock for {}: requested {}", "Insufficient stock for ABC: requested 9"), 0.7)

    def test_log_parser_json_and_traceback(self):
        e = parse_log_block('{"level":"ERROR","message":"User 0 not found","function":"load_user"}')
        self.assertEqual(e.level, "ERROR")
        self.assertEqual(e.function, "load_user")
        tb = 'Traceback (most recent call last):\n  File "users.py", line 7, in load_user\n    raise LookupError("missing user")\nLookupError: User 0 not found'
        e = parse_log_block(tb)
        self.assertTrue(e.stack_frames)
        self.assertEqual(e.stack_frames[-1].function, "load_user")
        self.assertEqual(e.exception_type, "LookupError")

    def test_log_parser_common_structured_formats(self):
        text = "\n".join([
            "2024-01-15 10:30:45,123 INFO [main] com.example.OrderService - Processing order: 12345",
            "2024-01-15 10:30:45.456 [worker] ERROR com.example.PaymentService - Payment failed",
            "Jan 15 10:30:45 localhost myapp[1234]: Application started",
            "timestamp=2024-01-15T10:30:45 level=ERROR message=Database connection failed",
            "07-08 10:12:01.123 1234 5678 E PcbTag: state=AwaitPcb deliveryId=abc timeout",
            "2024-01-15 10:30:45 ERROR com.example.Service - Exception occurred",
            "java.lang.NullPointerException: null",
            "    at com.example.Service.process(Service.java:42)",
        ])
        entries = parse_log_text(text)
        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0].logger, "com.example.OrderService")
        self.assertEqual(entries[0].message, "Processing order: 12345")
        self.assertEqual(entries[1].level, "ERROR")
        self.assertEqual(entries[2].logger, "myapp")
        self.assertEqual(entries[2].level, "INFO")
        self.assertEqual(entries[3].message, "Database connection failed")
        self.assertEqual(entries[4].logger, "PcbTag")
        self.assertEqual(entries[4].level, "ERROR")
        self.assertEqual(entries[4].fields["pid"], "1234")
        self.assertEqual(len(entries[5].raw.splitlines()), 3)

    def test_multilanguage_indexing_and_incremental_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Service.java").write_text('class Service { void run() { log.info("Java hello {}"); } }', encoding="utf-8")
            (root / "service.ts").write_text('export function run() { console.error("TS failed"); }', encoding="utf-8")
            (root / "main.go").write_text('package main\nimport "log"\nfunc Run() { log.Println("Go hello") }\n', encoding="utf-8")
            (root / "native.c").write_text('int run_native() { printf("C hello %d", 1); return 0; }\n', encoding="utf-8")
            (root / "engine.cpp").write_text('void Engine::Start() { spdlog::info("C++ started {}"); }\n', encoding="utf-8")
            (root / "generic.cpp").write_text('void Generic::Print() { printf("%d", 1); printf("%s%d", "x", 1); }\n', encoding="utf-8")
            (root / "app" / "src" / "main" / "jni" / "ncnn-20240820" / "include").mkdir(parents=True)
            (root / "app" / "src" / "main" / "jni" / "ncnn-20240820" / "include" / "Common.h").write_text('void Vendor::Print() { printf("Vendor hello %d", 1); }\n', encoding="utf-8")
            (root / "multiline.c").write_text('int multiline()\n{\n    printf("next-line brace");\n}\nprintf("global c log");\n', encoding="utf-8")
            (root / "widget.h").write_text('class Widget { public: void run() { std::cout << "header cpp log"; } };\n', encoding="utf-8")

            idx = Indexer(max_workers=2, incremental=True).build(root)
            self.assertTrue(any(fn.file.endswith("Service.java") for fn in idx.functions.values()))
            self.assertTrue(any(site.template == "TS failed" for site in idx.log_sites.values()))
            self.assertTrue(any(site.template == "Go hello" for site in idx.log_sites.values()))
            self.assertTrue(any(site.template == "C hello %d" for site in idx.log_sites.values()))
            self.assertTrue(any(site.template == "C++ started {}" for site in idx.log_sites.values()))
            self.assertFalse(any(site.template in {"%d", "%s%d"} for site in idx.log_sites.values()))
            self.assertFalse(any("ncnn-20240820" in site.file for site in idx.log_sites.values()))
            self.assertTrue(any(fn.name == "multiline" for fn in idx.functions.values()))
            next_line_site = next(site for site in idx.log_sites.values() if site.template == "next-line brace")
            self.assertIsNotNone(next_line_site.function_id)
            global_site = next(site for site in idx.log_sites.values() if site.template == "global c log")
            self.assertIsNone(global_site.function_id)
            header_site = next(site for site in idx.log_sites.values() if site.template == "header cpp log")
            self.assertTrue(header_site.id.startswith("log:cpp:"))

            (root / "service.ts").write_text('export function run() { return 1; }', encoding="utf-8")
            idx = Indexer(max_workers=2, incremental=True).build(root, existing_index=idx)
            self.assertFalse(any(site.file.endswith("service.ts") for site in idx.log_sites.values()))

    def test_locator_finds_template_source(self):
        idx = Indexer().build(SRC)
        cands = Locator(idx).locate(parse_log_block("ERROR [orders] Failed to update order 42"), top=3)
        self.assertTrue(cands)
        self.assertEqual(cands[0].function, "update_order")
        self.assertTrue(cands[0].file.endswith("orders.py"))

    def test_locator_finds_c_cpp_template_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native.c").write_text(
                "int run_native() { printf(\"C hello %d\", 1); return 0; }\n",
                encoding="utf-8",
            )
            (root / "engine.cpp").write_text(
                "void Engine::Start() { spdlog::info(\"C++ started {}\"); }\n",
                encoding="utf-8",
            )

            idx = Indexer().build(root)
            native_fid = next(fid for fid, fn in idx.functions.items() if fn.name == "run_native")
            idx.log_sites["generic:c"] = LogSite(
                id="generic:c",
                function_id=native_fid,
                level="info",
                template="%d",
                regex=template_to_regex("%d"),
                file=str(root / "native.c"),
                line=1,
            )
            self.assertFalse(Locator(idx).locate(parse_log_block("INFO 123"), top=3))

            c_cands = Locator(idx).locate(parse_log_block("INFO C hello 7"), top=3)
            self.assertTrue(c_cands)
            self.assertEqual(c_cands[0].function, "run_native")
            self.assertTrue(c_cands[0].file.endswith("native.c"))

            cpp_cands = Locator(idx).locate(parse_log_block("INFO C++ started engine"), top=3)
            self.assertTrue(cpp_cands)
            self.assertEqual(cpp_cands[0].function, "Engine::Start")
            self.assertTrue(cpp_cands[0].file.endswith("engine.cpp"))

    def test_incremental_updates_added_and_deleted_log_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app.py"
            source.write_text(
                "import logging\n"
                "log = logging.getLogger(__name__)\n"
                "\n"
                "def run():\n"
                "    log.info('first log')\n",
                encoding="utf-8",
            )

            idx = Indexer(incremental=True).build(root)
            self.assertTrue(any(site.template == "first log" for site in idx.log_sites.values()))

            source.write_text(
                "import logging\n"
                "log = logging.getLogger(__name__)\n"
                "\n"
                "def run():\n"
                "    log.info('second log')\n",
                encoding="utf-8",
            )
            idx = Indexer(incremental=True).build(root, existing_index=idx)
            self.assertFalse(any(site.template == "first log" for site in idx.log_sites.values()))
            self.assertTrue(any(site.template == "second log" for site in idx.log_sites.values()))

    def test_cli_analyze_extracts_runtime_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / ".loggraph" / "index.json"
            log_file = root / "app.log"
            (root / "app.py").write_text(
                "import logging\n"
                "log = logging.getLogger(__name__)\n"
                "\n"
                "def await_pcb():\n"
                "    log.error('pcb state=AwaitPcb deliveryId=%s duration=%sms timeout')\n"
                "\n"
                "def pcb_callback():\n"
                "    log.info('pcb callback received deliveryId=%s')\n",
                encoding="utf-8",
            )
            log_file.write_text(
                "ERROR [app] pcb state=AwaitPcb deliveryId=abc duration=15200ms timeout\n"
                "INFO [app] pcb callback received deliveryId=abc\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            from contextlib import redirect_stderr
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = cli_main(["init", str(root), "--out", str(out), "--progress-jsonl"])
            self.assertEqual(rc, 0)
            self.assertIn('"phase": "scan"', stderr.getvalue())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["analyze", str(root), "--log-file", str(log_file), "--index", str(out), "--all-lines", "--context", "1"])
            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertGreaterEqual(payload["runtime_findings"]["event_count"], 1)
            self.assertIn("timeout", payload["runtime_findings"]["event_types"])
            self.assertIn("project:pcb", payload["runtime_findings"]["event_types"])
            self.assertGreater(payload["event_profile_summary"]["learned_patterns"], 0)
            self.assertIn("deliveryId", payload["event_profile_summary"]["session_keys"])
            self.assertIn("# LogGraph Findings", payload["report_markdown"])
            self.assertTrue(payload["context_windows"])
            self.assertTrue(payload["runtime_findings"]["suggested_event_rules"])

            profile_path = root / ".loggraph" / "profile.yaml"
            profile_path.write_text(
                "session_keys:\n"
                "  - deliveryId\n"
                "events:\n"
                "  await_pcb:\n"
                "    type: await_pcb\n"
                "    patterns:\n"
                "      - AwaitPcb\n"
                "  pcb_result:\n"
                "    type: pcb_result\n"
                "    patterns:\n"
                "      - callback received\n"
                "expected_sequences:\n"
                "  delivery_success:\n"
                "    - await_pcb\n"
                "    - pcb_result\n",
                encoding="utf-8",
            )
            log_file.write_text("ERROR [app] pcb state=AwaitPcb deliveryId=abc duration=15200ms timeout\n", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["analyze", str(root), "--log-file", str(log_file), "--index", str(out), "--all-lines"])
            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["event_profile_summary"]["manual_profile"])
            self.assertTrue(payload["runtime_findings"]["session_timelines"])
            self.assertEqual(payload["runtime_findings"]["missing_events"][0]["missing"], ["pcb_result"])
            self.assertTrue(payload["runtime_findings"]["duration_stats"])
            self.assertTrue(payload["runtime_findings"]["hypotheses"])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["profile", "suggest", str(root), "--index", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn("session_keys:", stdout.getvalue())

            generated_profile = root / ".loggraph" / "generated-profile.yaml"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["profile", "init", str(root), "--index", str(out), "--out", str(generated_profile)])
            self.assertEqual(rc, 0)
            self.assertTrue(generated_profile.exists())

            baseline = root / "success.log"
            target = root / "failed.log"
            baseline.write_text(
                "ERROR [app] pcb state=AwaitPcb deliveryId=abc duration=800ms timeout\n"
                "INFO [app] pcb callback received deliveryId=abc\n",
                encoding="utf-8",
            )
            target.write_text("ERROR [app] pcb state=AwaitPcb deliveryId=abc duration=15200ms timeout\n", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["compare", str(root), "--baseline", str(baseline), "--target", str(target), "--index", str(out), "--all-lines"])
            self.assertEqual(rc, 0)
            self.assertIn("Missing in target", stdout.getvalue())
            self.assertIn("Duration anomalies", stdout.getvalue())
            self.assertIn("Target timed out before completing baseline path", stdout.getvalue())
            self.assertIn("pcb_result", stdout.getvalue())

    def test_cli_init_workers_and_no_incremental_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "index.json"
            (root / "app.py").write_text('def run():\n    print("hello")\n', encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli_main(["init", str(root), "--out", str(out), "--workers", "2", "--no-incremental"])

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["workers"], 2)
            self.assertFalse(payload["incremental"])
            self.assertTrue(out.exists())

    def test_evaluation_accuracy_gate(self):
        res = evaluate(SRC, CORPUS, top=3)
        self.assertEqual(res.total, 100)
        self.assertGreaterEqual(res.accuracy, 0.90)

    def test_stale_call_edges_removed_when_callee_file_deleted_incrementally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "caller.py").write_text(
                "import callee_file\n"
                "\n"
                "def caller():\n"
                "    callee_file.callee()\n",
                encoding="utf-8",
            )
            (root / "callee_file.py").write_text(
                "def callee():\n"
                "    return 42\n",
                encoding="utf-8",
            )

            idx = Indexer(incremental=True).build(root)
            caller_id = "py:caller:caller"
            callee_id = "py:callee_file:callee"

            self.assertTrue(any(edge.caller == caller_id for edge in idx.calls), "expected caller function edge")
            self.assertTrue(any(edge.callee == callee_id for edge in idx.calls), "expected resolved callee edge")

            (root / "callee_file.py").unlink()
            idx = Indexer(incremental=True).build(root, existing_index=idx)
            self.assertFalse(
                any(edge.caller == caller_id and edge.callee == callee_id for edge in idx.calls),
                "stale callee call edge should be removed on incremental rebuild",
            )

    def test_render_dot_contains_candidate_and_log(self):
        idx = Indexer().build(SRC)
        cands = Locator(idx).locate(parse_log_block("ERROR [users] User 0 not found"), top=1)
        dot = render_dot(idx, cands)
        self.assertIn("digraph LogGraph", dot)
        self.assertIn("load_user", dot)
        self.assertIn("User %s not found", dot)


if __name__ == "__main__":
    unittest.main()
