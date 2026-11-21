"""SOP ingestion pipeline (Phase 6 v2 — extends the original script per the
operator Phase 6 directive §5; blueprint 06 §3/§4).

Upgrades over the original ingest_sop_pinecone.py:
- Provider-abstracted embeddings (src/sop_embeddings.py): NIM-hosted bge-m3
  recommended — same key as the chat model, zero app-host RAM; the LOCAL
  path remains for dev-machine ingestion only.
- Metadata schema (§5): doc_id, title, sop_category, section_id, source,
  version, effective_date, approval_status, classification
  (synthetic/production), content_hash, access_classification — parsed from
  each document's structured header line.
- Fixed namespace (PINECONE_NAMESPACE, default "sops") — retrieval is
  namespace-restricted by construction; users can never select namespaces.
- Version tracking: re-ingesting a changed document purges that file's old
  chunks before upsert (one live version per file — invariant kept); the
  version is stamped on every chunk so citations can carry it.
- Duplicate handling: MD5 hash cache (unchanged) + deterministic chunk IDs.
- Verification report: after ingestion, per document: chunk count in the
  index + a direct fetch of one chunk; written to
  data/cache/sop_ingestion_report.json.
- Configurable knobs (blueprint C11): SOP_CHUNK_SIZE (600),
  SOP_CHUNK_OVERLAP (60), SOP_UPSERT_BATCH (100).

Never ingests anything outside data/policy/. Never prints API keys.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document

import os
import sys

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from src.sop_embeddings import embedding_settings, make_embeddings  # noqa: E402

# ---------------------------------------------- env knobs
CHUNK_SIZE = int(os.getenv("SOP_CHUNK_SIZE", "600"))
CHUNK_OVERLAP = int(os.getenv("SOP_CHUNK_OVERLAP", "60"))
UPSERT_BATCH = int(os.getenv("SOP_UPSERT_BATCH", "100"))
NAMESPACE = os.getenv("PINECONE_NAMESPACE", "sops")
POLICY_DIR = project_root / "data" / "policy"
HASH_CACHE_FILE = project_root / "data" / "cache" / "ingestion_hash_cache.json"
REPORT_FILE = project_root / "data" / "cache" / "sop_ingestion_report.json"

HEADER_PATTERNS = {
    "Version": re.compile(r"\*\*Version:\*\*\s*([^\n|]+)", re.I),
    "Effective Date": re.compile(r"\*\*Effective Date:\*\*\s*([^\n|]+)", re.I),
    "Status": re.compile(r"\*\*Status:\*\*\s*([^\n|]+)", re.I),
    "Category": re.compile(r"\*\*Category:\*\*\s*([^\n|]+)", re.I),
    "Source": re.compile(r"\*\*Source:\*\*\s*([^\n|]+)", re.I),
    "Access": re.compile(r"\*\*Access:\*\*\s*([^\n|]+)", re.I),
}


def parse_document_header(raw_text: str) -> dict:
    """Extract the structured header fields every SOP document must carry
    (§3: source, version, effective date, synthetic/approved status)."""
    meta = {}
    for key, pattern in HEADER_PATTERNS.items():
        m = pattern.search(raw_text)
        meta[key] = m.group(1).strip() if m else "unspecified"
    status = meta["Status"].lower()
    meta["classification"] = "synthetic" if "synthetic" in status else "production"
    meta["approval_status"] = ("synthetic-demo" if "synthetic" in status
                               else "demo-baseline" if "demo" in status else "approved")
    return meta


def split_markdown_sections(raw_text: str) -> list[tuple[str, str]]:
    """Split a markdown SOP into (section_title, section_text) pairs at
    '###'/'##' headers, keeping each header with its body. Dependency-light
    (no langchain text splitters needed — ingestion stays importable in the
    minimal test environment)."""
    lines = raw_text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        if re.match(r"^#{2,3}\s", line):
            sections.append((line.lstrip("# ").strip(), [line]))
        else:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip())
            for title, body in sections if "\n".join(body).strip()]


def cap_section(section_text: str, chunk_size: int, overlap: int) -> list[str]:
    """Size-cap one section at paragraph boundaries with character overlap."""
    if len(section_text) <= chunk_size:
        return [section_text]
    paragraphs = section_text.split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            current = para[:chunk_size]  # paragraph longer than a chunk: hard cap
        else:
            current = candidate
    if current:
        chunks.append(current)
    # apply overlap on interior boundaries
    out = []
    for i, c in enumerate(chunks):
        out.append((chunks[i - 1][-overlap:] + "\n" + c) if i and overlap else c)
    return out


def parse_and_chunk_document(doc_path: Path) -> tuple[list[Document], dict]:
    """Parse one document into chunks, preserving markdown structure and
    section identity; return (chunks, header_meta)."""
    raw_text = doc_path.read_text(encoding="utf-8")
    header_meta = parse_document_header(raw_text)
    title_line = next((ln for ln in raw_text.splitlines()
                       if ln.startswith("## ") and "Standard Operating" in ln), None)
    header_meta["title"] = (title_line or doc_path.stem).lstrip("# ").strip()

    chunks: list[Document] = []
    idx = 0
    for section_title, section_text in split_markdown_sections(raw_text):
        sec_id = re.match(r"([A-Z]\d+|\d+)\.", section_title)
        section_id = sec_id.group(1) if sec_id else (section_title[:40] or "general")
        for piece in cap_section(section_text, CHUNK_SIZE, CHUNK_OVERLAP):
            text = piece.strip()
            if not text:
                continue
            meta = {
                "doc_id": doc_path.stem,
                "title": header_meta["title"],
                "sop_category": header_meta.get("Category", "unspecified"),
                "section_id": str(section_id),
                "source": header_meta.get("Source", "unspecified"),
                "source_file": doc_path.name,
                "file_format": "MD",
                "version": header_meta.get("Version", "unspecified"),
                "effective_date": header_meta.get("Effective Date", "unspecified"),
                "approval_status": header_meta["approval_status"],
                "classification": header_meta["classification"],
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "access_classification": header_meta.get("Access", "internal-demo"),
                "chunk_index": idx,
            }
            chunks.append(Document(page_content=text, metadata=meta))
            idx += 1
    return chunks, header_meta


def plan_ingestion(current_files: dict[str, str], hash_cache: dict[str, str]) -> dict:
    """Pure ingestion planner (§5.8-9): decide what to delete, what to ingest
    and what to skip from the file->md5 map and the persisted cache.
    Duplicates (unchanged hash) are skipped; changed documents are purged and
    re-ingested (one live version per file); removed files are purged."""
    to_delete = sorted(set(hash_cache) - set(current_files))
    to_ingest = []
    skipped = []
    for name, digest in sorted(current_files.items()):
        if hash_cache.get(name) == digest:
            skipped.append(name)
        else:
            to_ingest.append(name)
    return {"purge_deleted": to_delete, "ingest": to_ingest, "skipped": skipped}


def _verify_doc(index_client, file_name: str, sample_vector: list, n_expected: int) -> tuple[int, bool]:
    """Post-upsert verification with retries: serverless namespaces are
    eventually consistent — stats/query can 404 for a few seconds right
    after the first upsert (live-found 2026-11-17)."""
    import time
    for attempt in range(6):
        try:
            stats = index_client.describe_index_stats(
                filter={"source_file": {"$eq": file_name}}, namespace=NAMESPACE)
            count = stats.namespaces.get(NAMESPACE, {}).get("vector_count", 0)
            sample = index_client.query(vector=sample_vector, top_k=1, namespace=NAMESPACE,
                                        filter={"source_file": {"$eq": file_name}},
                                        include_metadata=True)
            return int(count), bool(sample.get("matches"))
        except Exception as exc:  # NotFoundException: namespace not yet visible
            if "Namespace not found" in str(exc) and attempt < 5:
                time.sleep(3)
                continue
            return 0, False
    return 0, False


def build_report_entry(doc_id: str, header_meta: dict, chunks: list,
                       index_chunks: int, sample_ok: bool) -> dict:
    return {
        "doc_id": doc_id,
        "title": header_meta.get("title"),
        "version": header_meta.get("Version"),
        "approval_status": header_meta["approval_status"],
        "chunks_prepared": len(chunks),
        "chunks_in_index": index_chunks,
        "sample_fetch_ok": sample_ok,
        "verified": index_chunks == len(chunks) and sample_ok,
        "ingested_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _purge(index_client, file_name: str) -> None:
    """Purge one document's old chunks. A missing namespace (fresh index /
    first ingest) means there is nothing to purge — Pinecone returns 404
    instead of a no-op there, which is expected and ignored."""
    try:
        index_client.delete(filter={"source_file": {"$eq": file_name}},
                            namespace=NAMESPACE)
    except Exception as exc:
        if "Namespace not found" not in str(exc):
            raise


def main():
    from pinecone import Pinecone, ServerlessSpec
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")

    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise SystemExit("PINECONE_API_KEY is not configured — SOP ingestion "
                         "requires the operator-seeded Pinecone key")

    embeddings, index_name, dimension = make_embeddings()
    print(f"embedding provider={embedding_settings()[0]} dim={dimension} index={index_name}")

    pc = Pinecone(api_key=api_key)
    existing = pc.list_indexes().names()
    if index_name in existing:
        desc = pc.describe_index(index_name)
        if desc.dimension != dimension:
            print(f"dimension mismatch ({desc.dimension} != {dimension}); recreating index")
            pc.delete_index(index_name)
            existing = [n for n in existing if n != index_name]
    if index_name not in existing:
        print(f"creating serverless index {index_name} (dim {dimension}, cosine)")
        pc.create_index(name=index_name, dimension=dimension, metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region="us-east-1"))
    index_client = pc.Index(index_name)

    hash_cache = {}
    if HASH_CACHE_FILE.exists():
        try:
            hash_cache = json.loads(HASH_CACHE_FILE.read_text())
        except Exception:
            hash_cache = {}

    current_files = {p.name: hashlib.md5(p.read_bytes()).hexdigest()
                     for p in POLICY_DIR.glob("*.md")}
    plan = plan_ingestion(current_files, hash_cache)
    print(f"found {len(current_files)} SOP document(s): "
          f"{len(plan['ingest'])} to ingest, {len(plan['skipped'])} unchanged, "
          f"{len(plan['purge_deleted'])} purged")

    report = []
    updated_cache = dict(current_files)
    cache_modified = bool(plan["ingest"]) or bool(plan["purge_deleted"])

    for deleted_file in plan["purge_deleted"]:
        print(f"purging deleted document: {deleted_file}")
        _purge(index_client, deleted_file)

    by_name = {p.name: p for p in POLICY_DIR.glob("*.md")}
    for file_name in plan["ingest"]:
        file_path = by_name[file_name]
        print(f"ingesting: {file_name}")
        # one live version per file: purge this document's old chunks first
        _purge(index_client, file_name)
        chunks, header_meta = parse_and_chunk_document(file_path)
        if not chunks:
            print(f"  no valid chunks from {file_name}")
            updated_cache.pop(file_name, None)
            continue
        vectors = embeddings.embed_documents([c.page_content for c in chunks])
        assert len(vectors) == len(chunks), "embedding count mismatch"
        assert all(len(v) == dimension for v in vectors), \
            f"embedding dimension mismatch (expected {dimension})"
        ids = [f"{file_path.stem}-chunk-{c.metadata['chunk_index']}" for c in chunks]
        for i in range(0, len(chunks), UPSERT_BATCH):
            batch = range(i, min(i + UPSERT_BATCH, len(chunks)))
            index_client.upsert(
                namespace=NAMESPACE,
                vectors=[{"id": ids[j], "values": vectors[j],
                          # the chunk TEXT must live in the metadata under the
                          # store's text_key so langchain PineconeVectorStore can
                          # reconstruct Documents at retrieval time (live-found:
                          # without it every hit is silently skipped).
                          "metadata": {**chunks[j].metadata,
                                       "page_content": chunks[j].page_content},
                          } for j in batch],
            )
        # verification: count + sample fetch for this document (consistency-retried)
        index_chunks, sample_ok = _verify_doc(index_client, file_name, vectors[0], len(chunks))
        entry = build_report_entry(file_path.stem, header_meta, chunks,
                                   index_chunks, sample_ok)
        report.append(entry)
        print(f"  ingested {len(chunks)} chunks; index={index_chunks}; verified={entry['verified']}")

    if cache_modified:
        HASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HASH_CACHE_FILE.write_text(json.dumps(updated_cache, indent=4))
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps({"namespace": NAMESPACE, "documents": report},
                                          indent=2))
        print(f"ingestion report written: {REPORT_FILE}")
    else:
        print("index already up to date")


if __name__ == "__main__":
    main()
