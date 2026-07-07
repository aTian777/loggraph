import json
from pathlib import Path
from loggraph.indexer import Indexer
from loggraph.logs.parser import parse_log_block, parse_log_text
from loggraph.logs.templates import template_matches, similarity
from loggraph.matchers.locator import Locator
from loggraph.evaluation.runner import evaluate
from loggraph.graph.render import render_dot

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "fixtures/python_projects/service_app"
CORPUS = ROOT / "fixtures/labeled_logs/corpus.jsonl"


def test_indexer_extracts_functions_calls_and_logs():
    idx = Indexer().build(SRC)
    assert len(idx.functions) >= 10
    assert len(idx.log_sites) >= 10
    assert any(s.template == "Failed to update order %s" for s in idx.log_sites.values())
    assert any(fn.qualname == "PaymentService.charge" for fn in idx.functions.values())


def test_template_matching():
    assert template_matches("Failed to update order %s", "Failed to update order 42")
    assert template_matches("Database unavailable for order {}", "Database unavailable for order 500")
    assert similarity("Insufficient stock for {}: requested {}", "Insufficient stock for ABC: requested 9") > 0.7


def test_log_parser_json_and_traceback():
    e = parse_log_block('{"level":"ERROR","message":"User 0 not found","function":"load_user"}')
    assert e.level == "ERROR" and e.function == "load_user"
    tb = 'Traceback (most recent call last):\n  File "users.py", line 7, in load_user\n    raise LookupError("missing user")\nLookupError: User 0 not found'
    e = parse_log_block(tb)
    assert e.stack_frames and e.stack_frames[-1].function == "load_user"
    assert e.exception_type == "LookupError"


def test_locator_finds_template_source():
    idx = Indexer().build(SRC)
    cands = Locator(idx).locate(parse_log_block("ERROR [orders] Failed to update order 42"), top=3)
    assert cands
    assert cands[0].function == "update_order"
    assert cands[0].file.endswith("orders.py")


def test_evaluation_accuracy_gate():
    res = evaluate(SRC, CORPUS, top=3)
    assert res.total == 100
    assert res.accuracy >= 0.90


def test_render_dot_contains_candidate_and_log():
    idx = Indexer().build(SRC)
    cands = Locator(idx).locate(parse_log_block("ERROR [users] User 0 not found"), top=1)
    dot = render_dot(idx, cands)
    assert "digraph LogGraph" in dot
    assert "load_user" in dot
    assert "User %s not found" in dot
