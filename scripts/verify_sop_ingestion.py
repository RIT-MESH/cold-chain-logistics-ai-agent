"""Post-ingestion verification (Phase 6 step 6): real vector counts,
namespace state, metadata integrity, and a live retrieval through the
same store/threshold path the deployed agent uses. Never prints the key."""
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from pinecone import Pinecone

pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
desc = pc.describe_index("coldchain-sop-index-nim")
print(f"index: dim={desc.dimension} metric={desc.metric} ready={desc.status.get('ready')}")
index = pc.Index("coldchain-sop-index-nim")

stats = index.describe_index_stats()
total = 0
for ns, info in (stats.namespaces or {}).items():
    print(f"namespace {ns!r}: vector_count={info.get('vector_count')}")
    total += info.get("vector_count", 0)
print("total vectors:", total)

# metadata integrity: fetch every document's first chunk
report = json.loads(Path("data/cache/sop_ingestion_report.json").read_text())
for doc in report["documents"]:
    res = index.query(
        vector=[0.0] * desc.dimension, top_k=1, namespace="sops",
        filter={"source_file": {"$eq": doc["doc_id"] + ".md"}},
        include_metadata=True)
    match = (res.get("matches") or [{}])[0]
    md = match.get("metadata", {})
    ok = all(md.get(k) for k in ("doc_id", "title", "version", "section_id",
                                 "classification", "access_classification"))
    print(f"{doc['doc_id']}: metadata_ok={ok} version={md.get('version')} "
          f"classification={md.get('classification')}")

# live retrieval through the AGENT'S code path
import src.agent_tools as tools
from unittest.mock import patch
os.environ.setdefault("PINECONE_NAMESPACE", "sops")
tools._sop_store.cache_clear()
result = tools.search_compliance_sop.invoke(
    {"query": "temperature excursion corrective action fresh perishables"})
print("--- agent-path retrieval (first 400 chars) ---")
print(result[:400])
