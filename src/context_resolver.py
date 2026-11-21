"""Deterministic conversation-context resolution (operator directive 2026-11-21 §3).

Resolves which shipment(s) a question refers to, using ONLY the trusted
per-request message history that LibreChat already sends with every chat
request (api/main.py convert_history). There is NO global mutable state and
NOTHING is persisted across requests: two conversations (or two users) are
isolated by construction, because resolution never sees messages that were
not part of the current request.

Trust boundary: resolved ids are validated against the strict UC-\\d{6}
pattern and only ever parameterize FIXED, guard-valid SQL templates
(src/evidence_prefetch.py). Conversation text can influence WHICH fixed
template runs — never the SQL structure itself; an invented id simply
returns "No records matched".

Resolution precedence (directive §3):
1. An explicit shipment id in the CURRENT user message always wins.
2. Referent expressions ("this shipment", "it", "its temperature", "them",
   "the shipment", "same") resolve the most recent UC-id from prior trusted
   turns of THIS conversation, scanned backward.
3. Two distinct ids in the current message (or preserved from a prior
   comparison report when the question is comparative) => comparison scope.
4. Ambiguity (no id found, or too many candidate ids in the immediately
   preceding assistant turn) => NO resolution: the prefetch no-ops and the
   stock ReAct flow asks for clarification naturally.

Intent classification (deterministic keyword heuristics, same style as the
Phase 6 SOP intent table) drives validator dispatch (M3 fix) and evidence
scope. Intents:
  incident_sop          — incident question that also asks for procedural guidance
  incident              — incident/problem question (no SOP request)
  comparison            — two-shipment comparison
  temperature_history   — explicit temperature/reading history request
  humidity_history      — explicit humidity history request
  shipment_report       — any other resolved-shipment question
  general               — no shipment resolved; stock flow, no prefetch
"""
import re

from langchain_core.messages import AIMessage, HumanMessage

SHIPMENT_ID_RE = re.compile(r"\bUC-\d{6}\b", re.IGNORECASE)

# Referent expressions that point back at an earlier shipment mention.
# Word-ish matching keeps "its" from matching inside "visits"/"transits"
# (the \b boundaries plus the surrounding token checks below handle the
# common conversational forms observed in the operator's drill).
REFERENT_RES = (
    re.compile(r"\bthis\s+(?:shipment|cargo|delivery|load|unit)\b", re.I),
    re.compile(r"\bthat\s+(?:shipment|cargo|delivery|load|unit)\b", re.I),
    re.compile(r"\bthe\s+(?:same|current|aforementioned)\s+(?:shipment|cargo|delivery|load)\b", re.I),
    re.compile(r"\b(?:its|it|them)\b", re.I),
    re.compile(r"\bwhat\s+about\s+(?:it|them|this|that)\b", re.I),
    re.compile(r"\bcompare\s+(?:it|them|both)\b", re.I),
    re.compile(r"\b(?:were|was|are|is)\s+there\s+any\s+in\s+(?:it|them|this|that)\b", re.I),
)

COMPARISON_RE = re.compile(
    r"\b(compar\w*|versus|\bvs\.?\b|differen\w*|contrast|better|riskier|safer|"
    r"worse|greater|which\s+(?:was|is|had)\s+(?:more|less|greater|the)\b)", re.I)

INCIDENT_RE = re.compile(
    r"\b(incident\w*|excursion\w*|problem\w*|issue\w*|alert\w*|anomal\w*|"
    r"what\s+happened|happen\w*|wrong|fail\w*|breach\w*)\b", re.I)

SOP_RE = re.compile(
    r"\b(sop|s\.o\.p|procedure\w*|corrective|mitigat\w*|recommend\w*|guidance|"
    r"escalat\w*|should\s+(?:be\s+)?done|what\s+(?:to\s+do|action\w*)|action\s+plan|"
    r"sensor|logger|stopped\s+reporting|disconnected|handover|handoff|seal|"
    r"custody)\b", re.I)

TEMP_HISTORY_RE = re.compile(
    r"\b(temperature\w*|readings?|thermal|hot\w*|cold\w*|highest|lowest|"
    r"maximum|minimum|average|warm\w*|history\s+for|over\s+time|trend\w*)\b", re.I)

HUMIDITY_RE = re.compile(r"\b(humidity|humid|moisture|condensation)\b", re.I)

MAX_COMPARE_IDS = 2


class Resolution:
    """Immutable per-request resolution result."""

    __slots__ = ("ids", "intent", "referred", "ambiguous")

    def __init__(self, ids, intent, referred=False, ambiguous=False):
        self.ids = list(ids)          # resolved UC-ids, order of appearance (<= 2)
        self.intent = intent          # one of the intents above
        self.referred = referred      # True when the id came from prior turns
        self.ambiguous = ambiguous     # True when referents existed but no safe id

    @property
    def active_id(self):
        return self.ids[0] if self.ids else None


def extract_ids(text: str) -> list:
    """Ordered unique UC-xxxxxx ids in a text (canonical uppercase)."""
    ids = []
    for m in SHIPMENT_ID_RE.finditer(text or ""):
        sid = m.group(0).upper()
        if sid not in ids:
            ids.append(sid)
    return ids


def has_referent(text: str) -> bool:
    return any(r.search(text or "") for r in REFERENT_RES)


def prior_texts(convo):
    """Prior (non-current) trusted turns, most recent first."""
    prior = convo[:-1] if convo and convo[-1][0] == "human" else convo
    prior = list(prior)
    prior.reverse()
    return prior


def _classify_intent(question: str, ids: list) -> str:
    """Deterministic intent from the CURRENT question plus resolved ids.
    Order: comparison > SOP (procedural guidance about a shipment always
    carries the incident context in its evidence) > humidity > temperature
    history > incident > plain report."""
    q = question or ""
    if len(ids) >= 2 and COMPARISON_RE.search(q):
        return "comparison"
    if SOP_RE.search(q):
        return "incident_sop"
    if HUMIDITY_RE.search(q):
        return "humidity_history"
    if TEMP_HISTORY_RE.search(q):
        return "temperature_history"
    if INCIDENT_RE.search(q):
        return "incident"
    return "shipment_report"


def resolve_conversation(messages) -> Resolution:
    """Resolve shipment referents + intent for one request.

    `messages` is the converted LangGraph message list for THIS request
    (system prompt messages are ignored; only trusted human/assistant turns
    are scanned — LibreChat server-side history, not client metadata).
    """
    # Conversation texts in order, current user question last.
    convo = []
    for m in messages:
        kind = getattr(m, "type", "")
        if kind == "human":
            convo.append(("human", str(getattr(m, "content", "") or "")))
        elif kind == "ai":
            convo.append(("ai", str(getattr(m, "content", "") or "")))

    current_question = convo[-1][1] if convo and convo[-1][0] == "human" else ""

    # 1. Explicit ids in the current message always win. A comparative
    #    follow-up ("compare it with UC-003241") additionally pulls the
    #    referent's id from history (up to the 2-id comparison scope).
    explicit = extract_ids(current_question)[:MAX_COMPARE_IDS]
    if explicit:
        ids = list(explicit)
        if len(ids) < MAX_COMPARE_IDS and has_referent(current_question):
            for _kind, text in reversed(prior_texts(convo)):
                for hid in extract_ids(text):
                    if hid not in ids:
                        ids.append(hid)
                        break
                if len(ids) >= MAX_COMPARE_IDS:
                    break
        return Resolution(ids, _classify_intent(current_question, ids))

    # 2. Referent expressions resolve from prior turns (backward scan).
    if has_referent(current_question):
        for _kind, text in prior_texts(convo):
            ids = extract_ids(text)
            if not ids:
                continue
            if len(ids) == 1:
                return Resolution(ids, _classify_intent(current_question, ids), referred=True)
            if len(ids) == MAX_COMPARE_IDS:
                # Two ids in the most recent mentioning turn: a comparative
                # follow-up ("which had greater risk?") keeps both; a
                # non-comparative one treats the FIRST as the subject.
                if COMPARISON_RE.search(current_question):
                    return Resolution(ids, _classify_intent(current_question, ids), referred=True)
                return Resolution(ids[:1], _classify_intent(current_question, ids[:1]), referred=True)
            # > 2 distinct ids in one turn: too ambiguous to resolve safely.
            return Resolution([], "general", referred=True, ambiguous=True)
        # Referent present but no id anywhere in trusted history: unresolved.
        return Resolution([], "general", referred=True, ambiguous=True)

    # 3. Implied referent (§11 drill message 5: "Were there any incidents?"):
    #    a shipment-scoped question (incident/temperature/humidity/SOP/
    #    comparison keywords) with no explicit id resolves the conversation's
    #    ACTIVE shipment — but only when the history is UNAMBIGUOUS (a single
    #    distinct id, or exactly the two ids of a prior comparison report for
    #    a comparative question). Multiple unrelated ids -> ambiguous -> the
    #    stock flow asks for clarification.
    if any(rx.search(current_question) for rx in
           (INCIDENT_RE, TEMP_HISTORY_RE, HUMIDITY_RE, SOP_RE, COMPARISON_RE)):
        comparative = bool(COMPARISON_RE.search(current_question))
        history_ids = []
        for _kind, text in convo:
            for hid in extract_ids(text):
                if hid not in history_ids:
                    history_ids.append(hid)
        if len(history_ids) == 1:
            return Resolution(history_ids, _classify_intent(current_question, history_ids),
                              referred=True)
        if len(history_ids) == MAX_COMPARE_IDS and comparative:
            return Resolution(history_ids, _classify_intent(current_question, history_ids),
                              referred=True)
        if len(history_ids) == MAX_COMPARE_IDS:
            # Two ids in history, non-comparative question: the FIRST id is
            # the conversation's primary subject (same rule as the explicit
            # referent branch).
            return Resolution(history_ids[:1],
                              _classify_intent(current_question, history_ids[:1]),
                              referred=True)
        if history_ids:
            return Resolution([], "general", referred=True, ambiguous=True)

    # 4. No id, no referent, no shipment-scoped keywords: general question,
    #    stock flow.
    return Resolution([], "general")


def prefetch_applicable(resolution: Resolution) -> bool:
    """The deterministic fast path applies only to resolved-shipment
    questions. General/unresolved questions follow the stock ReAct flow
    (which will ask for clarification when the referent was ambiguous)."""
    return bool(resolution.ids)
