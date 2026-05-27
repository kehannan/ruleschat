"""
Client-side RAG retrieval against OpenAI vector stores.

The OpenAI Responses API bundles file_search into the LLM call (one
server-side round-trip). For non-OpenAI models routed via OpenRouter we
have to do retrieval explicitly: search the vector store, format the
chunks into a context block, then send the result + the question to the
non-OpenAI model.

Public surface:
    retrieve_chunks(client, vector_store_ids, query, max_results_per_store)
        → list[dict] of {section, page, text}
    format_chunks_as_context(chunks) → str  (system-prompt-ready block)
"""

from typing import List, Dict, Any
import logging

from openai import OpenAI


def retrieve_chunks(
    client: OpenAI,
    vector_store_ids: List[str],
    query: str,
    max_results_per_store: int = 20,
) -> List[Dict[str, Any]]:
    """
    Search each vector store for chunks relevant to `query`, return a flat list.

    Each chunk has keys: `text` (str), `filename` (str), `score` (float).
    Caller gets at most `max_results_per_store × len(vector_store_ids)` chunks
    total — same shape the eval harness uses, so the prompt assembly stays
    consistent across eval and production paths.
    """
    all_chunks: List[Dict[str, Any]] = []
    for vs_id in vector_store_ids:
        try:
            page = client.vector_stores.search(
                vector_store_id=vs_id,
                query=query,
                max_num_results=max_results_per_store,
            )
            for result in page.data:
                # Each result.content is a list of {type, text} entries — join
                # the text bits. Different OpenAI SDK versions structure this
                # slightly differently; handle both shapes defensively.
                text_parts = []
                for c in (getattr(result, "content", []) or []):
                    t = getattr(c, "text", None)
                    if t:
                        text_parts.append(t)
                    elif isinstance(c, dict) and c.get("text"):
                        text_parts.append(c["text"])
                chunk_text = "\n".join(text_parts).strip()
                if not chunk_text:
                    continue
                all_chunks.append({
                    "text": chunk_text,
                    "filename": getattr(result, "filename", "") or "",
                    "score": getattr(result, "score", 0.0) or 0.0,
                })
        except Exception as e:
            logging.warning(f"⚠️ vector_stores.search failed for {vs_id}: {e}")
            continue
    return all_chunks


def format_chunks_as_context(chunks: List[Dict[str, Any]]) -> str:
    """
    Format retrieved chunks into a single context block for the system prompt.

    Empty input → empty string (caller can skip the system-prompt addendum).
    """
    if not chunks:
        return ""
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        header = f"[Chunk {i}"
        if chunk.get("filename"):
            header += f" — {chunk['filename']}"
        header += "]"
        blocks.append(f"{header}\n{chunk['text']}")
    return "\n\n".join(blocks)
