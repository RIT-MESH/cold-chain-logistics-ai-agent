"""Phase 0 (objective 4, offline): exercise the REAL LangGraph reasoner->tools
loop with a MOCKED LLM so tool-calling plumbing is verified without NVIDIA NIM
credentials or billed requests.

The NIM endpoint's tool-calling loop was live-verified 2026-11-03 (10/10,
architecture-review/NVIDIA_NIM_API_VERIFICATION.md); one live conversation
through THIS gateway (Phase 0 gate C13, re-targeted from Ollama to NIM by
R6-1) remains pending.
"""
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage

FAKE_KEY = "fake-key-for-offline-test-only"


@pytest.fixture(scope="module")
def orchestrator():
    import os
    import sys
    prev = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "NVIDIA_API_KEY")}
    os.environ["LLM_PROVIDER"] = "NVIDIA_NIM"
    os.environ["NVIDIA_API_KEY"] = FAKE_KEY
    try:
        sys.modules.pop("src.orchestrator", None)
        import src.orchestrator as module
        yield module
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import sys as _sys
        _sys.modules.pop("src.orchestrator", None)


class _StubResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"current_weather": {"temperature": 21.5, "windspeed": 9.0}}


def _run_tool_conversation(orchestrator, monkeypatch, tool_calls):
    """Drive the graph with scripted LLM responses; returns the final message list."""
    responses = iter(
        [AIMessage(content="", tool_calls=tool_calls), AIMessage(content="Done summarizing.")]
    )

    class FakeLLM:
        def bind_tools(self, tools):  # noqa: N802 (kept for interface parity)
            return self

        def invoke(self, messages, config=None, **kwargs):
            return next(responses)

    monkeypatch.setattr(orchestrator, "llm_with_tools", FakeLLM())
    result = orchestrator.fde_agent.invoke(
        {"messages": [HumanMessage("Weather at the port?")]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 10},
    )
    return result["messages"]


def test_weather_tool_call_round_trip(orchestrator, monkeypatch):
    monkeypatch.setattr("src.agent_tools.requests.get", lambda *a, **k: _StubResponse())
    messages = _run_tool_conversation(
        orchestrator,
        monkeypatch,
        [{"name": "fetch_corridor_conditions", "args": {"latitude": 33.77, "longitude": -118.19}, "id": "call_wx", "type": "tool_call"}],
    )
    tool_messages = [m for m in messages if m.__class__.__name__ == "ToolMessage"]
    assert len(tool_messages) == 1, "expected exactly one tool execution"
    assert "21.5" in tool_messages[0].content, tool_messages[0].content
    assert "CURRENT WEATHER" in tool_messages[0].content  # non-historical labeling intact
    assert messages[-1].content == "Done summarizing."


def test_sql_tool_routes_through_guard(orchestrator, monkeypatch):
    """A disallowed query must be blocked by the SQL guard INSIDE the real tool,
    before any database connection is attempted."""
    messages = _run_tool_conversation(
        orchestrator,
        monkeypatch,
        [{"name": "query_telemetry_db", "args": {"sql_query": "DELETE FROM FDE_VIEWS.VW_SHIPMENTS"}, "id": "call_sql", "type": "tool_call"}],
    )
    tool_messages = [m for m in messages if m.__class__.__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content.startswith("SECURITY BLOCK"), tool_messages[0].content


def test_sop_tool_unconfigured_fails_safe(orchestrator, monkeypatch):
    """Without PINECONE_API_KEY the SOP tool must return an explanatory error,
    never fabricated compliance text."""
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    import src.agent_tools as tools
    tools._sop_store.cache_clear()  # drop any cached store from earlier tests
    messages = _run_tool_conversation(
        orchestrator,
        monkeypatch,
        [{"name": "search_compliance_sop", "args": {"query": "fresh perishables range"}, "id": "call_sop", "type": "tool_call"}],
    )
    tool_messages = [m for m in messages if m.__class__.__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    assert "PINECONE_API_KEY is not configured" in tool_messages[0].content, tool_messages[0].content