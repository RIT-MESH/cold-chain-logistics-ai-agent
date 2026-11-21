"""Diagnostic: raw similarity scores (no threshold) for the SOP query
through the same embedding + store path, to calibrate SOP_MIN_SCORE."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sop_embeddings import make_embeddings
from pinecone import Pinecone

embeddings, index_name, dim = make_embeddings()
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index(index_name)

for query in ("temperature excursion corrective action fresh perishables",
              "what to do if temperature exceeds the range",
              "sensor stops reporting logger disconnected",
              "custody handover seal verification"):
    vec = embeddings.embed_query(query)
    res = index.query(vector=vec, top_k=3, namespace="sops", include_metadata=True)
    print(f"query: {query}")
    for m in res.get("matches", []):
        md = m.get("metadata", {})
        print(f"  score={m['score']:.4f} {md.get('doc_id')} §{md.get('section_id')}")
