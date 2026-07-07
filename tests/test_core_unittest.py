import unittest
from pathlib import Path
from loggraph.indexer import Indexer
from loggraph.logs.parser import parse_log_block
from loggraph.logs.templates import template_matches, similarity
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

    def test_locator_finds_template_source(self):
        idx = Indexer().build(SRC)
        cands = Locator(idx).locate(parse_log_block("ERROR [orders] Failed to update order 42"), top=3)
        self.assertTrue(cands)
        self.assertEqual(cands[0].function, "update_order")
        self.assertTrue(cands[0].file.endswith("orders.py"))

    def test_evaluation_accuracy_gate(self):
        res = evaluate(SRC, CORPUS, top=3)
        self.assertEqual(res.total, 100)
        self.assertGreaterEqual(res.accuracy, 0.90)

    def test_render_dot_contains_candidate_and_log(self):
        idx = Indexer().build(SRC)
        cands = Locator(idx).locate(parse_log_block("ERROR [users] User 0 not found"), top=1)
        dot = render_dot(idx, cands)
        self.assertIn("digraph LogGraph", dot)
        self.assertIn("load_user", dot)
        self.assertIn("User %s not found", dot)


if __name__ == "__main__":
    unittest.main()
