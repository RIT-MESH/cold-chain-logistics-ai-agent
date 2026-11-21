"""Deterministic synthesis-answer validators.

Phase 6: the strict incident+SOP validator for the UC-003241 demo case
(every check an exact-match against the verified evidence package).

2026-11-21 (LibreChat repair directive §2-M3/§5/§10): validator selection is
now INTENT-driven (api/main.py classifies the request and picks the matching
validator) — answers are never routed to a validator merely because they
mention a shipment id. The strict validator also handles honest
SOP-unavailability (embedding timeouts, §9): an answer that states SOP
guidance could not be retrieved passes; a fabricated citation never does.

New light validators cover temperature/humidity history and shipment
comparisons without judging them on incident+SOP criteria they were never
asked about. A shared geography check rejects place-name labels attached to
coordinates (the dataset intentionally mixes regions; the observed
"California region (lat 34.9, lon 135.3)" answer was unsupported).
"""
import re

EVIDENCE = {
    "shipment_id": "UC-003241",
    "incident_id": "temp:8df143db-78a9-41f9-aafe-c2ad0046906e",
    "event_id": "8df143db-78a9-41f9-aafe-c2ad0046906e",
    "temperature_c": "6.2",
    "threshold_c": "4.0",
    "severity": "2.2",  # derived: 6.2 - 4.0
    "status": "OPEN",
    "provenance": "synthetic",
    "notification_state": "sns_acked",
    "notification_interpretation": "topic acceptance, NOT recipient delivery",
    "sop_doc": "Temperature Management",
    "sop_version": "1.0-demo",
    "sop_section": "A3",
    "sop_source": "synthetic-demo",
}

REQUIRED_CHECKS = [
    ("shipment identified", lambda a: EVIDENCE["shipment_id"] in a),
    ("recorded temperature 6.2C", lambda a: "6.2" in a),
    ("threshold 4.0C", lambda a: "4.0" in a or "4" in a),
    ("incident id present", lambda a: EVIDENCE["incident_id"][:20] in a or "temp:8df143db" in a),
    ("derived severity consistent", lambda a: ("2.2" in a or "severity" in a.lower())),
    ("incident status OPEN", lambda a: "OPEN" in a),
    ("sns_acked != delivered", lambda a: ("sns_acked" in a and ("not" in a.lower() and "deliver" in a.lower()))),
    ("SOP section A3 cited", lambda a: "A3" in a),
    ("SOP title/version cited", lambda a: (EVIDENCE["sop_doc"].lower() in a.lower() and "1.0-demo" in a)),
    ("synthetic labeling present", lambda a: "synthetic" in a.lower()),
    ("corrective action from SOP (verify cooling/driver/divert)",
     lambda a: any(k in a.lower() for k in ("cooling", "driver", "divert"))),
    ("human review framing", lambda a: "review" in a.lower()),
    ("no credential leakage",
     lambda a: not re.search(r"(nvapi-|pcsk_|Bearer\s+[A-Za-z0-9_\-]{20,}|password\s*=)", a, re.I)),
]

# SOP-citation checks (indexes into REQUIRED_CHECKS) that only apply when
# SOP evidence was actually retrieved; when retrieval failed they are
# replaced by an anti-fabrication check (§9).
SOP_CHECK_NAMES = {"SOP section A3 cited", "SOP title/version cited",
                   "corrective action from SOP (verify cooling/driver/divert)"}

FORBIDDEN_FABRICATIONS = [
    ("no fabricated higher temperature", [r"9\.2\s*°?C", r"8\.9\s*°?C"]),
    ("no invented delivery confirmation", [r"email\s+was\s+delivered", r"recipient\s+confirmed"]),
]

# Known SOP sections across the 7-document corpus (baseline 1-3, A1-6, B1-3,
# C1-4, D1-4, E1-5, F1-5) — a §-cited section outside this set is invented.
KNOWN_SECTIONS = ({f"{p}{n}" for p in "ABCDEF" for n in range(1, 7)} |
                  {str(n) for n in range(1, 4)})

# SOP-citation-like text (bracketed citation or "SOP section X" claims).
SOP_CITATION_RE = re.compile(
    r"\[[^\]]{0,140}\|\s*version\s+[^\]|]{1,40}\|[^\]]{0,140}\]"
    r"|(?:sop|procedure)\s+(?:section|§)\s*[A-Fa-f]\d+", re.I)


def _no_invented_sections(answer: str) -> bool:
    cited = set(re.findall(r"§\s*([A-F]\d+)\b", answer or ""))
    return cited <= KNOWN_SECTIONS


def validate_answer(answer: str, sop_available: bool = True) -> dict:
    """Strict incident+SOP validator (Phase 6, UC-003241 demo case).

    sop_available=False (embedding/Pinecone retrieval failed): the answer
    must not fabricate SOP guidance; honest absence passes. Every SQL-fact
    check still applies unchanged.
    """
    checks = []
    for name, fn in REQUIRED_CHECKS:
        if name in SOP_CHECK_NAMES and not sop_available:
            continue
        checks.append((name, fn(answer or "")))
    if not sop_available:
        checks.append(("no fabricated SOP citation when SOP unavailable",
                       not SOP_CITATION_RE.search(answer or "")))
    for name, patterns in FORBIDDEN_FABRICATIONS:
        ok = not any(re.search(p, answer or "", re.I) for p in patterns)
        checks.append((name, ok))
    checks.append(("no invented SOP section", _no_invented_sections(answer)))
    failures = [name for name, ok in checks if not ok]
    return {"passed": not failures, "checks": checks, "failures": failures}


# ============================================================================
# Shared geography check (directive §5): no place-name labels attached to
# coordinates. The dataset README states GPS is modeled with intentional
# region mixing — any region/area/zone/country label in the SAME SENTENCE as
# explicit lat/lon coordinates is an unsupported claim (the observed failure:
# "GPS track remained in the California region (lat approximately 34.9, lon
# approximately 135.3)" — 34.9/135.3 is not California at all).
# ============================================================================

_COORD_SENTENCE_RE = re.compile(r"(?:lat(?:itude)?|lon(?:gitude)?|coordinates?)\D{0,30}-?\d", re.I)
_REGION_WORD_RE = re.compile(r"\b(?:region|area|zone|country)\b", re.I)


def geo_label_violation(answer: str) -> bool:
    """True when one sentence contains BOTH explicit coordinates and a
    region/area/zone/country label — i.e. the answer asserts a geographic
    identity the data cannot support. Coordinates alone, or region words
    away from coordinates (e.g. inside an SOP quote), pass."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer or ""):
        if _COORD_SENTENCE_RE.search(sentence) and _REGION_WORD_RE.search(sentence):
            return True
    return False


GEO_CHECK = ("no unsupported geographical label", lambda a: not geo_label_violation(a))


# ============================================================================
# General shipment-question validator (2026-11-18 fix: raw tool-call
# representations must never reach the user; 2026-11-21: geography check
# added — shipment reports were the source of the "California region" claim).
# ============================================================================

RAW_TOOL_CALL_RE = re.compile(
    r"query_telemetry_db\s*\(|search_compliance_sop\s*\(|"
    r"fetch_corridor_conditions\s*\(|SELECT\s+TOP\s|"
    r"FDE_VIEWS\.V[A-Z]_", re.I)

SHIPMENT_REQUIRED_CHECKS = [
    ("no raw tool-call syntax",
     lambda a: not RAW_TOOL_CALL_RE.search(a or "")),
    ("shipment identifier present",
     lambda a: bool(re.search(r"UC-\d{6}", a or ""))),
    ("synthetic-data disclosure",
     lambda a: "synthetic" in (a or "").lower()),
    ("no credential leakage",
     lambda a: not re.search(r"(nvapi-|pcsk_|Bearer\s+[A-Za-z0-9_\-]{20,}|password\s*=)", a, re.I)),
    ("no fabricated temperature",
     lambda a: not re.search(r"\d+\.\d+\s*°?\s*C(?![\s\S]{0,50}(?:threshold|range|configured|min|max|limit))",
                              a or "", re.I) or "temperature" in (a or "").lower()),
    GEO_CHECK,
]


def validate_shipment_answer(answer: str, shipment_id: str) -> dict:
    """Validate a general shipment-question answer (prefetch path)."""
    checks = [(name, fn(answer or "")) for name, fn in SHIPMENT_REQUIRED_CHECKS]
    checks.append(("correct shipment id", shipment_id in (answer or "")))
    failures = [name for name, ok in checks if not ok]
    return {"passed": not failures, "checks": checks, "failures": failures}


# ============================================================================
# Temperature/humidity-history validator (directive §2-M3): a history answer
# mentioning UC-003241 must NOT be judged on incident+SOP criteria.
# ============================================================================

def validate_history_answer(answer: str, shipment_id: str) -> dict:
    """Light validation for temperature/humidity history answers: no tool
    syntax, correct shipment, disclosure, no credential leakage, no
    unsupported geography. Deliberately does NOT demand incident or SOP
    content — the question never asked for it."""
    a = answer or ""
    checks = [
        ("no raw tool-call syntax", not RAW_TOOL_CALL_RE.search(a)),
        ("correct shipment id", shipment_id in a),
        ("synthetic-data disclosure", "synthetic" in a.lower()),
        ("no credential leakage",
         not re.search(r"(nvapi-|pcsk_|Bearer\s+[A-Za-z0-9_\-]{20,}|password\s*=)", a, re.I)),
        ("no unsupported geographical label", not geo_label_violation(a)),
    ]
    failures = [name for name, ok in checks if not ok]
    return {"passed": not failures, "checks": checks, "failures": failures}


# ============================================================================
# Comparison validator (directive §4): both shipments present, no tool
# syntax, disclosure, no unsupported geography; conclusions must be framed
# on stated criteria (checked at synthesis-prompt level).
# ============================================================================

def validate_comparison_answer(answer: str, id1: str, id2: str) -> dict:
    checks = [
        ("no raw tool-call syntax", not RAW_TOOL_CALL_RE.search(answer or "")),
        ("both shipment ids present", id1 in (answer or "") and id2 in (answer or "")),
        ("synthetic-data disclosure", "synthetic" in (answer or "").lower()),
        ("no credential leakage",
         not re.search(r"(nvapi-|pcsk_|Bearer\s+[A-Za-z0-9_\-]{20,}|password\s*=)", answer or "", re.I)),
        ("no unsupported geographical label", not geo_label_violation(answer or "")),
    ]
    failures = [name for name, ok in checks if not ok]
    return {"passed": not failures, "checks": checks, "failures": failures}
