"""SOP embedding provider abstraction (Phase 6, blueprint 06 §2/§3).

Alignment invariant (guidelines §5.6): embedding model ↔ dimension ↔ index
name must match. The provider knob SOP_EMBEDDING_PROVIDER selects one of:

- NIM (recommended): hosted nvidia/nemotron-3.5-embed-1b via the SAME
  NVIDIA NIM endpoint and API key as the chat model
  (integrate.api.nvidia.com/v1, OpenAI-compatible /embeddings). 2048-dim
  (LIVE-PROBED 2026-11-17: BAAI/bge-m3 is NO LONGER SERVED on NIM —
  nemotron-3.5-embed-1b measured at 2048 dims; operator-approved 2048).
  Zero new credentials, zero app-host RAM footprint (the local bge-m3
  needs ~2.5-3 GB torch+weights and MUST NOT run in the gateway container
  — blueprint 06 §2). input_type is sent per call: "passage" for
  ingestion, "query" for retrieval (operator directive §4).
- LOCAL: HuggingFace bge-m3 (ingestion on the dev machine only — never the
  app host). 1024-dim.
- OPENAI: OpenAIEmbeddings (1536-dim) — requires a SEPARATE OpenAI key and
  bill (explicitly not covered by NIM, blueprint 06 §2).

Index naming (alignment): coldchain-sop-index-nim / fde-sop-index-local /
fde-sop-index-openai. Dimension self-heal stays in the ingestion script.
"""
import os


def embedding_settings():
    """Resolve (provider, index_name, dimension) from env — the single
    source of truth shared by ingestion and retrieval (never mixed)."""
    provider = os.getenv("SOP_EMBEDDING_PROVIDER", "NIM").strip().upper()
    if provider == "NIM":
        return provider, os.getenv("PINECONE_INDEX_NAME", "coldchain-sop-index-nim"), 2048
    if provider == "LOCAL":
        return provider, os.getenv("PINECONE_INDEX_NAME", "fde-sop-index-local"), 1024
    if provider == "OPENAI":
        return provider, os.getenv("PINECONE_INDEX_NAME", "fde-sop-index-openai"), 1536
    raise ValueError("SOP_EMBEDDING_PROVIDER must be NIM, LOCAL or OPENAI")


class NIMEmbeddings:
    """OpenAI-compatible embeddings client against the NVIDIA NIM endpoint
    (same NVIDIA_API_KEY as the chat model — no new credential). Implements
    the langchain Embeddings contract; sends bge-m3's input_type per call
    (query vs passage) which OpenAIEmbeddings cannot vary per invocation.
    Bounded timeout (10 s) — a stuck embeddings call must not hang a chat
    request."""

    def __init__(self, model=None, timeout=10):
        from openai import OpenAI  # already a project dependency
        self._model = model or os.getenv("SOP_EMBEDDING_MODEL", "nvidia/nemotron-3.5-embed-1b")
        self._timeout = timeout
        self._client = OpenAI(
            base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            api_key=os.environ["NVIDIA_API_KEY"],
        )

    def _embed(self, texts, input_type):
        if not isinstance(texts, (list, tuple)):
            texts = [texts]
        # Size limit: NIM rejects very large batch payloads; cap per request.
        out = []
        for i in range(0, len(texts), 64):
            resp = self._client.embeddings.create(
                model=self._model, input=list(texts[i:i + 64]),
                extra_body={"input_type": input_type, "truncate": "NONE"},
                timeout=self._timeout,
            )
            out.extend(d.embedding for d in resp.data)
        return out

    def embed_documents(self, texts):
        """Ingestion path: passages."""
        return self._embed(list(texts), "passage")

    def embed_query(self, text):
        """Retrieval path: query embedding."""
        return self._embed([text], "query")[0]


def make_embeddings():
    """Build the configured embeddings object (LangChain-compatible)."""
    provider, index_name, dimension = embedding_settings()
    if provider == "NIM":
        if not os.getenv("NVIDIA_API_KEY"):
            raise RuntimeError("NVIDIA_API_KEY must be configured for NIM embeddings")
        return NIMEmbeddings(), index_name, dimension
    if provider == "LOCAL":
        from langchain_huggingface import HuggingFaceEmbeddings
        model = os.getenv("Local_Embedding_Model", "BAAI/bge-m3")
        return (HuggingFaceEmbeddings(model_name=model, model_kwargs={"device": "cpu"}),
                index_name, dimension)
    if provider == "OPENAI":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(), index_name, dimension
    raise ValueError("SOP_EMBEDDING_PROVIDER must be NIM, LOCAL or OPENAI")
