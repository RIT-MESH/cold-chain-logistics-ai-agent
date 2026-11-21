import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# ==========================================
# 1. SETUP & PATH RESOLUTION
# ==========================================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parents[0]

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.agent_tools import query_telemetry_db, fetch_corridor_conditions, search_compliance_sop
from src.evidence_prefetch import prefetch_enabled, prefetch_node

load_dotenv(project_root / ".env")

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

# ==========================================
# 2. FACTORY INITIALIZATION: AGENT REASONER LLM
# ==========================================

def create_llm():
    """Build the NVIDIA NIM reasoner. There is no other provider and no silent fallback.

    Operator decision 2026-11-08 (blueprint R6-1): NVIDIA NIM is the
    application's ONLY runtime LLM; the former OLLAMA_CLOUD / OPENAI /
    DEEPSEEK / OLLAMA_LOCAL branches were removed and unsupported values
    hard-fail. Endpoint behavior live-verified 2026-11-03 (10/10 PASS):
    architecture-review/NVIDIA_NIM_API_VERIFICATION.md

    Operator decision 2026-11-14 (blueprint P-8, ledger R6-2): the production
    model within NIM is z-ai/glm-5.3-flash — post-repair qualification
    14/15 complete_correct, zero API failures, every guard rejection
    recovered (docs/evidence/NVIDIA_NIM_POST_REPAIR_QUALIFICATION.md).
    """
    from langchain_openai import ChatOpenAI

    provider = (os.getenv("LLM_PROVIDER") or "NVIDIA_NIM").strip().upper()
    if provider != "NVIDIA_NIM":
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}. The only supported provider is NVIDIA_NIM")
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is required when LLM_PROVIDER=NVIDIA_NIM")
    base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    # Avoid accidentally transmitting credentials to an untrusted endpoint.
    if base_url != "https://integrate.api.nvidia.com/v1":
        raise ValueError("NVIDIA_BASE_URL must be https://integrate.api.nvidia.com/v1")
    model = os.getenv("NVIDIA_MODEL", "z-ai/glm-5.3-flash").strip()
    if not model:
        raise ValueError("NVIDIA_MODEL must not be empty")
    # Latency: deepseek verification turns 46.7-79.3 s (2026-11-03); glm-5.3-flash
    # qualification battery lookup ~10 s, multi-tool answers 78-205 s median over
    # 2-4 turns (2026-11-09) — 180 s covers one turn with headroom; total answer
    # time is bounded by the graph recursion limit.
    # max_tokens must stay generous: reasoning tokens consume the completion
    # budget (verification finding F-3) and small caps return empty content.
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=key,
        temperature=0,
        timeout=float(os.getenv("NVIDIA_REQUEST_TIMEOUT_SECONDS", "180")),
        max_retries=int(os.getenv("NVIDIA_MAX_RETRIES", "2")),
        max_tokens=int(os.getenv("NVIDIA_MAX_OUTPUT_TOKENS", "4096")),
    )


llm = create_llm()

fde_tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
llm_with_tools = llm.bind_tools(fde_tools)

# Phase 6 operator decision (2026-11-17): SYTHESIS_MODEL selects the model for
# the FINAL EVIDENCE-GROUNDED SYNTHESIS TURN ONLY — used when the
# deterministic prefetch already supplied all evidence (no tool calls
# needed). Unset = the stock GLM ReAct flow everywhere (rollback = unset +
# restart). This is a FIXED configuration rule, NOT automatic switching:
# prefetched incident+SOP questions -> synthesis model; everything else ->
# GLM with tools, unchanged. No tools are bound in the synthesis path
# (unqualified tool behavior cannot occur).
SYNTHESIS_MODEL = os.getenv("SYTHESIS_MODEL", "").strip()
synthesis_llm = None
if SYNTHESIS_MODEL:
    # Same locked endpoint/key rules as create_llm(); only the model id
    # differs. No tools bound in this path.
    # 2026-11-21 (directive §8, bounded waits): the synthesis turn gets its
    # OWN shorter timeout/retry budget — Nemotron Lightning measures 0.7-10 s
    # per turn, so a stuck synthesis fails fast into the deterministic
    # evidence fallback instead of burning the glm-class 300 s x 3 attempts.
    from langchain_openai import ChatOpenAI
    synthesis_llm = ChatOpenAI(
        model=SYNTHESIS_MODEL,
        base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.getenv("NVIDIA_API_KEY", ""),
        temperature=0,
        timeout=float(os.getenv("SYNTHESIS_REQUEST_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("SYNTHESIS_MAX_RETRIES", "1")),
        max_tokens=int(os.getenv("NVIDIA_MAX_OUTPUT_TOKENS", "4096")),
    )

# ==========================================
# 3. GRAPH ARCHITECTURE ASSEMBLY
# ==========================================
PREFETCH_MARKER = "PRE-FETCHED EVIDENCE"


def _has_prefetch_evidence(messages) -> bool:
    return any(getattr(m, "type", "") == "system" and PREFETCH_MARKER in str(getattr(m, "content", ""))
               for m in messages)


def reasoning_node(state: AgentState):
    # Evidence-grounded synthesis path: when the prefetch supplied all
    # evidence AND the operator configured a synthesis model, answer WITHOUT
    # tools in a single turn (the historical tool-selection failure modes of
    # a synthesis-only model cannot occur). Any other request: stock ReAct.
    if synthesis_llm is not None and _has_prefetch_evidence(state["messages"]):
        response = synthesis_llm.invoke(state["messages"])
    else:
        response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

print("⚙️ Compiling LangGraph FDE Orchestrator...")
graph_builder = StateGraph(AgentState)
graph_builder.add_node("reasoner", reasoning_node)
graph_builder.add_node("tools", ToolNode(fde_tools))

# Phase 6 optimization (operator directive 2026-11-17 — PREPARED, default
# OFF): when ENABLE_EVIDENCE_PREFETCH=true, a deterministic pre-retrieval
# node runs the guarded SQL incident lookup + hardened SOP retrieval for a
# UC-xxxxxx question BEFORE the first NIM call, collapsing a combined
# incident+SOP answer from >= 3 model turns to 1 synthesis turn. OFF (or a
# question without a shipment id) = the stock ReAct flow, unchanged.
if prefetch_enabled():
    graph_builder.add_node("prefetch", prefetch_node)
    graph_builder.add_edge(START, "prefetch")
    graph_builder.add_edge("prefetch", "reasoner")
else:
    graph_builder.add_edge(START, "reasoner")
graph_builder.add_conditional_edges("reasoner", tools_condition)
graph_builder.add_edge("tools", "reasoner")

fde_agent = graph_builder.compile()  # API/LibreChat sends explicit conversation history; no global in-memory session store

# CLI testing: run API gateway and connect through LibreChat.
# A separate interactive CLI is not maintained in this version.
