"""Restricted read-only SQL validator for unified cold-chain views.

Phase 5 (2026-11-17): the allowlist extends to the Phase 4 incident/
notification views so the agent can investigate the ingested event stream
(blueprint 03 §B1, gap G-EP-3 — guard, tool docstring and tests ship
together). ADDITIVE ONLY: every pre-Phase-5 restriction is unchanged —
schema-qualified single-view SELECTs, modification/CTE/join/UNION blocks,
unified.* base-table blocks, cross-database blocks, TOP 1-100 row caps.
"""
import sqlglot
from sqlglot import exp

ALLOWED_VIEWS = {
    # Unified synthetic dataset views (Phase 0-2)
    "vw_shipments", "vw_telemetry", "vw_custody", "vw_alerts", "vw_sensors", "vw_vehicles", "vw_routes",
    # Phase 4 ingested event stream + derived notification state (Phase 5)
    "vw_telemetry_events", "vw_telemetry_incidents", "vw_incident_notifications", "vw_event_link_validation",
}

def validate_query(query: str) -> str:
    if len(query) > 6000:
        raise ValueError("SQL query too long")
    try:
        expressions = sqlglot.parse(query, read="tsql")
    except sqlglot.errors.ParseError as exc:
        raise ValueError("Invalid SQL syntax") from exc
    if len(expressions) != 1 or not isinstance(expressions[0], exp.Select):
        raise ValueError("Only one SELECT statement is allowed")
    statement = expressions[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Union, exp.Intersect, exp.Except, exp.Into)
    if any(isinstance(node, forbidden) for node in statement.walk()):
        raise ValueError("Disallowed SQL operation")
    tables = list(statement.find_all(exp.Table))
    if not tables or any(t.name.lower() not in ALLOWED_VIEWS or (t.db or '').lower() != 'fde_views' or t.catalog for t in tables):
        raise ValueError("Only FDE_VIEWS approved unified views may be queried")
    # No CTEs, joins, subqueries, external table functions, or SELECT *.
    if any(isinstance(n, (exp.Subquery, exp.Join, exp.With)) or (isinstance(n, exp.Star) and not isinstance(n.parent, exp.Count)) for n in statement.walk()):
        raise ValueError("Complex SQL forms are not supported")
    if len(tables) != 1:
        raise ValueError("Exactly one approved view is required")
    if statement.args.get("limit") is None:
        # Aggregate-only queries return one row. All regular queries need TOP.
        expressions = statement.expressions
        if len(expressions) == 1 and isinstance(expressions[0], (exp.Count, exp.Alias)) and (isinstance(expressions[0], exp.Count) or isinstance(expressions[0].this, exp.Count)):
            return query
        raise ValueError("Use TOP to cap SQL results (maximum 100 rows)")
    limit = statement.args["limit"].expression
    try:
        amount = int(limit.name)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("TOP must use a numeric literal")
    if amount < 1 or amount > 100:
        raise ValueError("TOP must be between 1 and 100")
    return query
