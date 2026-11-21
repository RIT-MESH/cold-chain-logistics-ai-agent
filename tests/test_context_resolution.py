"""Conversation-context resolution tests (operator directive 2026-11-21 §3,
§12): referent resolution within one conversation, comparison scope,
ambiguity handling, shipment switching, and isolation-by-construction.
All offline — the resolver only sees the message list handed to it."""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.context_resolver import (
    extract_ids, has_referent, prefetch_applicable, resolve_conversation,
)


def _convo(*turns):
    """Build a message list from (role, text) tuples."""
    msgs = []
    for role, text in turns:
        if role == "user":
            msgs.append(HumanMessage(content=text))
        else:
            msgs.append(AIMessage(content=text))
    return msgs


# ------------------------------------------------------------ explicit ids
def test_explicit_id_wins_over_history():
    msgs = _convo(("user", "hi"), ("ai", "Report for UC-003916 ..."),
                  ("user", "any problem with UC-003241?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003241"]
    assert r.referred is False
    assert r.intent == "incident"


def test_two_explicit_ids_with_compare_language_is_comparison():
    r = resolve_conversation(_convo(
        ("user", "compare shipments UC-003916 and UC-003241, which is riskier?")))
    assert r.ids == ["UC-003916", "UC-003241"]
    assert r.intent == "comparison"


# ------------------------------------------------------------ referent resolution
def test_followup_this_shipment_resolves_from_history():
    # The exact operator drill: report turn, then "any problem in this shipment?"
    msgs = _convo(("user", "hi"), ("ai", "Hi! ..."),
                  ("user", "UC-003916 report"),
                  ("ai", "Shipment UC-003916 departed... (synthetic test data)"),
                  ("user", "any problem in this shipment?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003916"]
    assert r.referred is True
    assert r.intent == "incident"
    assert prefetch_applicable(r) is True


def test_its_temperature_followup_resolves_active_shipment():
    msgs = _convo(("user", "UC-003916 report"),
                  ("ai", "Report for UC-003916: accepted..."),
                  ("user", "what was its highest temperature?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003916"]
    assert r.intent == "temperature_history"


def test_were_there_any_incidents_followup():
    msgs = _convo(("user", "UC-003916 report"),
                  ("ai", "Report for UC-003916: accepted..."),
                  ("user", "were there any incidents?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003916"]
    assert r.intent == "incident"


def test_compare_it_with_explicit_second_id_merges_history():
    # "Compare it with UC-003241": explicit id + referent -> both ids
    # (the message's own id first, the history referent second).
    msgs = _convo(("user", "UC-003916 report"),
                  ("ai", "Report for UC-003916 ..."),
                  ("user", "compare it with UC-003241"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003241", "UC-003916"]
    assert r.intent == "comparison"


def test_comparative_followup_after_comparison_report_keeps_both():
    msgs = _convo(("user", "compare UC-003916 and UC-003241"),
                  ("ai", "UC-003916 ... UC-003241 ..."),
                  ("user", "which shipment had greater temperature risk?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003916", "UC-003241"]
    assert r.intent == "comparison"


# ------------------------------------------------------------ ambiguity + switching
def test_more_than_two_ids_in_last_turn_is_ambiguous():
    msgs = _convo(("ai", "UC-001111, UC-002222 and UC-003333 all shipped"),
                  ("user", "what about it?"))
    r = resolve_conversation(msgs)
    assert r.ids == []
    assert r.ambiguous is True
    assert prefetch_applicable(r) is False  # stock flow -> model asks


def test_referent_without_any_id_is_ambiguous():
    r = resolve_conversation(_convo(("user", "hello"), ("ai", "hi there"),
                                    ("user", "what about it?")))
    assert r.ids == [] and r.ambiguous is True


def test_shipment_switch_inside_conversation():
    msgs = _convo(("user", "UC-003916 report"),
                  ("ai", "Report for UC-003916 ..."),
                  ("user", "UC-003241 report"),
                  ("ai", "Report for UC-003241 ..."),
                  ("user", "any problem in this shipment?"))
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003241"]  # switched — most recent id wins


# ------------------------------------------------------------ isolation (§12)
def test_no_cross_conversation_leakage_by_construction():
    # A fresh conversation never mentions UC-003916: the resolver, which only
    # sees THIS request's messages, cannot resolve anything.
    r = resolve_conversation(_convo(("user", "hi")))
    assert r.ids == [] and r.intent == "general"
    r2 = resolve_conversation(_convo(("user", "report for UC-000101"),
                                     ("ai", "Report for UC-000101 ..."),
                                     ("user", "any problem in this shipment?")))
    assert r2.ids == ["UC-000101"]  # unrelated conversation resolves its own


def test_system_messages_are_never_scanned():
    msgs = [SystemMessage(content="system prompt mentions UC-999999"),
            HumanMessage(content="any problem in this shipment?")]
    r = resolve_conversation(msgs)
    assert r.ids == []  # the PROMPT (and any injected system text) is ignored


def test_extract_ids_and_referents():
    assert extract_ids("about uc-003916 and UC-003916") == ["UC-003916"]
    assert has_referent("what was its highest temperature?") is True
    assert has_referent("UC-003916 report") is False
