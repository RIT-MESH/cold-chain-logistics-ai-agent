"""Debug: what does the agent's PineconeVectorStore actually return for
the same query (raw (doc, score) pairs, unfiltered)?"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.agent_tools as tools

store = tools._sop_store()
hits = store.similarity_search_with_score(
    "temperature excursion corrective action fresh perishables",
    k=tools.SOP_TOP_K, filter=tools.SOP_ACCESS_FILTER)
print("raw hits:", len(hits))
for doc, score in hits:
    print(f"  score={score!r} doc={doc.metadata.get('doc_id')} §{doc.metadata.get('section_id')}")
print("threshold:", tools.SOP_MIN_SCORE)
