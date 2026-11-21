"""Regression tests for the 2026-11-09 SQL tool-interface repair:
1. The query_telemetry_db tool documentation explicitly teaches the T-SQL
   rules whose absence caused the final-validation failures (FDE_VIEWS.
   schema prefix, TOP 1-100, no LIMIT, real view columns, read-only), with
   valid examples and NO hardcoded shipment-specific answers.
2. The bounded SQL rejection recovery terminates wasteful retry loops:
   repeated identical rejections are stopped, distinct corrections are
   budgeted, exhaustion returns a clear failure instruction, valid queries
   are untouched, and rejected SQL is NEVER rewritten or executed.
3. The F-B success-loop metering (2026-11-14, blueprint P-8): identical
   queries repeated after a successful execution are stopped without
   limiting distinct valid queries.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "architecture-review"))

from src import agent_tools
from src.agent_tools import query_telemetry_db, register_sql_rejection, SQLRejectionBudget

PROMPT_PATH = ROOT / "src/prompts/system_prompt.txt"


@pytest.fixture(autouse=True)
def fresh_budget():
    agent_tools._sql_retry_budget.set(SQLRejectionBudget())
    yield
    agent_tools._sql_retry_budget.set(None)


# ------------------------------------------------- documentation (§1) ------
def test_docstring_teaches_schema_prefix_and_top():
    desc = query_telemetry_db.description
    assert "FDE_VIEWS." in desc, "must show the mandatory schema prefix"
    assert "TOP 1 to TOP 100" in desc, "must state the mandatory TOP bounds"
    assert "LIMIT is NOT valid T-SQL" in desc
    assert "SELECT *" in desc  # named as rejected
    assert "READ-ONLY" in desc
    assert "TOP 1-100" in PROMPT_PATH.read_text(encoding="utf-8"), \
        "system prompt must reinforce prefix + TOP"


def test_docstring_examples_are_valid_tsql():
    from nim_validation_common import execute_query
    desc = query_telemetry_db.description
    examples = [ln.strip("- ").strip() for ln in desc.splitlines()
                if ln.strip().startswith("- SELECT")]
    assert len(examples) >= 3, "must provide at least three examples"
    for ex in examples:
        # every example must pass the REAL guard
        _, outcome = execute_query(ex)
        assert outcome != "blocked", f"example must be guard-valid: {ex}"


def test_docstring_documents_real_view_columns():
    """The docstring must match the REAL FDE_VIEWS definitions — including
    VW_SHIPMENTS' arrival columns the old docstring omitted."""
    desc = query_telemetry_db.description
    for col in ("expected_arrival_at", "arrived_at", "temperature_c", "humidity_pct",
                "severity", "seal_intact", "weather_severity", "receiver_verdict"):
        assert col in desc, f"missing real column {col}"


def test_docstring_hardcodes_no_shipment_answers():
    desc = query_telemetry_db.description
    assert "UC-003241" not in desc, "no shipment-specific data in the instructions"
    assert "'<shipment_id>'" in desc, "examples must use a placeholder, not real answers"


# --------------------------------------------- bounded recovery (§2) --------
def test_identical_repeated_rejection_is_stopped():
    sql = "SELECT cargo_type FROM VW_SHIPMENTS WHERE shipment_id = 'X'"
    first = register_sql_rejection(sql)
    assert first is None, "first rejection returns the raw guard error"
    second = register_sql_rejection(sql)
    assert second is not None and "already rejected" in second
    assert "Do not repeat it" in second


def test_distinct_rejections_are_budgeted():
    assert register_sql_rejection("q1") is None
    assert register_sql_rejection("q2") is None
    assert register_sql_rejection("q3") is None
    assert register_sql_rejection("q4") is None
    exhausted = register_sql_rejection("q5")
    assert exhausted is not None and "budget" in exhausted
    assert "Stop issuing" in exhausted


def test_budget_is_per_request_context():
    agent_tools._sql_retry_budget.set(None)  # fresh request context
    register_sql_rejection("SELECT a FROM VW_X")
    agent_tools._sql_retry_budget.set(None)  # next request starts fresh
    assert register_sql_rejection("SELECT a FROM VW_X") is None


def test_tool_uses_budget_in_rejection_path():
    # first identical rejection: raw guard error (DELETE is rejected at the
    # single-SELECT check before the forbidden-operation check)
    out1 = query_telemetry_db.invoke({"sql_query": "DELETE FROM FDE_VIEWS.VW_SHIPMENTS"})
    assert out1.startswith("SECURITY BLOCK:")
    # exact repeat: bounded-recovery instruction, not the raw error
    out2 = query_telemetry_db.invoke({"sql_query": "DELETE FROM FDE_VIEWS.VW_SHIPMENTS"})
    assert "already rejected" in out2 and "Do not repeat it" in out2


def test_valid_queries_do_not_consume_the_budget():
    # valid guard-passing query: budget untouched (execution fails locally —
    # no SQL Server — but the guard path is exercised)
    out = query_telemetry_db.invoke({"sql_query":
        "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"})
    assert out == "Database query failed; inspect service logs."
    # the rejection budget was not consumed by the valid query
    assert register_sql_rejection("DELETE FROM FDE_VIEWS.VW_SHIPMENTS") is None


def test_rejected_sql_is_never_rewritten():
    # genuinely guard-rejected query (missing TOP): three attempts return raw
    # guard error / bounded-recovery instructions — never rewritten,
    # executable SQL
    for _ in range(3):
        out = query_telemetry_db.invoke({"sql_query":
            "SELECT cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"})
    assert "FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'" not in out
    assert out.startswith("SECURITY BLOCK")
    assert "already rejected" in out  # bounded recovery engaged, not a rewrite


def test_limit_query_never_loops_and_is_never_rewritten():
    """Quirk pinned (2026-11-14): sqlglot's tsql reader parses LIMIT, so the
    guard PASSES 'LIMIT 5' and the real SQL Server would reject it
    server-side (no security impact — still one read-only approved view).
    Locally execution fails; an identical repeat must be stopped by the F-B
    meter instead of looping to the recursion limit."""
    sql = "SELECT cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X' LIMIT 5"
    out1 = query_telemetry_db.invoke({"sql_query": sql})
    assert out1 == "Database query failed; inspect service logs."
    out2 = query_telemetry_db.invoke({"sql_query": sql})
    assert "already executed" in out2 and "Do not repeat it" in out2
    assert "FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'" not in out2


# ----------------------------- F-B success-loop metering (2026-11-14) ------
def test_identical_repeated_valid_query_is_stopped():
    """Post-repair qualification F-B: a model looping on IDENTICAL SUCCESSFUL
    queries reached the recursion limit (lightning delay r2: 9 x ok). The exact
    repeat must return a do-not-repeat instruction, not execute again."""
    sql = "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"
    out1 = query_telemetry_db.invoke({"sql_query": sql})
    assert out1 == "Database query failed; inspect service logs."  # first submission executes
    out2 = query_telemetry_db.invoke({"sql_query": sql})
    assert "already executed" in out2 and "Do not repeat it" in out2


def test_distinct_valid_queries_are_never_blocked():
    """F-B must not limit DISTINCT successful queries — legitimate multi-view
    investigation stays unmetered."""
    for sid in ("X", "Y", "Z"):
        out = query_telemetry_db.invoke({"sql_query":
            f"SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = '{sid}'"})
        assert out == "Database query failed; inspect service logs."


def test_success_metering_does_not_consume_rejection_budget():
    """The F-B executed-queries registry is separate from rejection metering."""
    sql = "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"
    query_telemetry_db.invoke({"sql_query": sql})
    query_telemetry_db.invoke({"sql_query": sql})  # F-B instruction
    assert register_sql_rejection("DELETE FROM FDE_VIEWS.VW_SHIPMENTS") is None


def test_success_metering_is_per_request_context():
    """A new request starts with a fresh registry — the same query may execute
    again for a different conversation."""
    sql = "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"
    agent_tools._sql_retry_budget.set(None)  # simulate the next request context
    out = query_telemetry_db.invoke({"sql_query": sql})
    assert out == "Database query failed; inspect service logs."  # executes again, no instruction


def test_repeated_query_with_different_whitespace_is_detected():
    """Normalization: spacing differences do not bypass the meter."""
    sql_a = "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'X'"
    sql_b = "SELECT   TOP   5   cargo_type  FROM  FDE_VIEWS.VW_SHIPMENTS  WHERE  shipment_id  =  'X'"
    query_telemetry_db.invoke({"sql_query": sql_a})
    out = query_telemetry_db.invoke({"sql_query": sql_b})
    assert "already executed" in out


def test_docstring_teaches_no_duplicate_queries():
    desc = query_telemetry_db.description
    assert "Never repeat an identical query" in desc, "F-B rule must be in the tool description"
