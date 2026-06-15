#!/usr/bin/env python3
"""
Setup script for the ASL Q&A vector store.

The ASL Q&A (Scott Romanowski's "Questions & Answers, Clarifications, & Errata")
is a flat, two-column Q&A errata document that supersedes the older Perry Sez.
Each entry has the structure:
    <rule refs>          e.g. "A1.23 & A25.222"
    <question text>      (may span multiple lines, often ends with "?")
    A.<answer text>      (may span multiple lines, often ends with source tags
                          like "[An97]", "[J1]")

Chunks are one-or-more complete Q&A entries, never splitting mid-entry.
Entries are packed up to MAX_CHUNK_SIZE chars. Each entry's rule refs and
starting page are embedded as metadata: {A1.23|A25.222|p17} <body>

Creates a separate vector store from the rulebook so both can be queried
together (file_search accepts multiple vector_store_ids). Config is tracked
under a top-level "qa_versions" key parallel to "versions".

This document is two-column, so we reuse extract_text_two_column() from
setup_responses_api.py. Each page carries a running header on its first line
(e.g. "Chapter A Official Q&A:") and a "page N" footer line; both are stripped.
"""

import os
import sys
import re
import json
import time
import logging
import tempfile
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

from setup_responses_api import extract_text_two_column

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

MAX_CHUNK_SIZE = 4000
# ASL has 1- and 2-letter chapters (A, B, ..., HF, FB, SC, AP, ...)
# plus optional single-letter subsection suffixes (A1.23a, FB17.6194b).
RULE_REF = r'\b[A-Z]{1,2}\d*\.\d+(?:\.\d+)?[a-z]?\b'
CHAPTER_MARKER = re.compile(r'^\s*Chapter\s+[A-Z]{1,2}\s*$')
PAGE_FOOTER = re.compile(r'^\s*page\s+\d+\s*$', re.IGNORECASE)

# In this document each Q&A entry begins with a leading run of rule refs
# *inline* with the question text, e.g. "A4.132 & B3.4 Can moving units...".
# A new entry starts at a line that opens with such a ref-run followed by the
# question (a capital letter / quote / paren) or end-of-line. Requiring the
# question text after the refs avoids false-splitting answer lines that merely
# wrap onto a rule citation (e.g. "B26.44; G9.4]. Is this...").
ENTRY_HEADER = re.compile(
    r'^\s*(' + RULE_REF + r'(?:\s*(?:,|&|and)\s*' + RULE_REF + r')*)'
    r'(?=\s+[A-Z(“"\'‘]|\s*$)'
)


def entry_header_refs(line: str) -> List[str] | None:
    """If `line` begins a new Q&A entry, return its leading rule refs; else None."""
    m = ENTRY_HEADER.match(line)
    if not m:
        return None
    return re.findall(RULE_REF, m.group(1))


def strip_header_footer(page_text: str) -> List[str]:
    """
    Return the page's content lines with the running header and footer removed.

    The two-column reconstruction places the page's running header (e.g.
    "Chapter A Official Q&A:") on the first line — drop it. The footer is a
    standalone "page N" line — drop any such line.
    """
    lines = page_text.split('\n')
    # Drop the leading running header (first non-empty line).
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        lines = lines[1:]
    return [ln for ln in lines if not PAGE_FOOTER.match(ln)]


def extract_qa_entries(pdf_path: str) -> List[Tuple[List[str], str, int]]:
    """
    Walk every line of the PDF and split into Q&A entries at each rule-ref
    header. Content before the first header (TOC, cover, intro) is skipped.
    Returns list of (rule_refs, body, page_num).
    """
    logging.info(f"Extracting Q&A entries from {pdf_path}")

    entries: List[Tuple[List[str], str, int]] = []
    current_refs: List[str] | None = None
    current_body: List[str] = []
    current_page: int | None = None
    started = False  # becomes True at the first "Chapter X" divider, skipping
                     # the cover/TOC/intro prose that precedes the real Q&A.

    def flush():
        nonlocal current_refs, current_body
        if current_refs is not None:
            body = '\n'.join(current_body).strip()
            if body:
                entries.append((current_refs, body, current_page))
        current_refs = None
        current_body = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        logging.info(f"  Processing {total_pages} pages...")

        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            if page_num % 25 == 0:
                logging.info(f"  {page_num}/{total_pages} pages")
            text = extract_text_two_column(page) or ''

            for line in strip_header_footer(text):
                if CHAPTER_MARKER.match(line):
                    # Chapter boundary flushes the in-progress entry. The first
                    # one also marks the end of the cover/TOC/intro prose.
                    flush()
                    started = True
                    continue
                if not started:
                    continue
                refs = entry_header_refs(line)
                if refs is not None:
                    flush()
                    current_refs = refs
                    current_body = [line.strip()]
                    current_page = page_num
                elif current_refs is not None:
                    current_body.append(line)

    flush()

    logging.info(f"✅ Extracted {len(entries)} Q&A entries")
    return entries


def format_entry(refs: List[str], body: str, page_num: int) -> str:
    refs_str = '|'.join(refs) if refs else 'unknown'
    return f"{{{refs_str}|p{page_num}}} {body}"


def split_oversized_body(
    refs: List[str], body: str, page_num: int, budget: int
) -> List[str]:
    """
    Break a too-long body across multiple chunks at sentence boundaries.
    Each emitted chunk carries the same metadata prefix so citations still work.
    """
    prefix = f"{{{'|'.join(refs) if refs else 'unknown'}|p{page_num}}} "
    effective = budget - len(prefix) - 2  # leave room for prefix + separator

    sentences = re.split(r'(?<=[.!?])\s+', body)
    parts: List[str] = []
    buf: List[str] = []
    buf_size = 0

    for sent in sentences:
        slen = len(sent) + 1
        if buf_size + slen > effective and buf:
            parts.append(prefix + ' '.join(buf))
            buf = [sent]
            buf_size = slen
        else:
            buf.append(sent)
            buf_size += slen

    if buf:
        parts.append(prefix + ' '.join(buf))
    return parts


def group_entries_into_chunks(
    entries: List[Tuple[List[str], str, int]],
) -> List[str]:
    """Pack formatted entries into chunks up to MAX_CHUNK_SIZE. Oversized entries are split at sentence boundaries."""
    chunks: List[str] = []
    buffer: List[str] = []
    buffer_size = 0
    split_count = 0

    def flush():
        nonlocal buffer, buffer_size
        if buffer:
            chunks.append('\n\n'.join(buffer))
            buffer = []
            buffer_size = 0

    for refs, body, page_num in entries:
        formatted = format_entry(refs, body, page_num)
        size = len(formatted) + 2

        if size > MAX_CHUNK_SIZE:
            flush()
            parts = split_oversized_body(refs, body, page_num, MAX_CHUNK_SIZE)
            chunks.extend(parts)
            split_count += 1
            continue

        if buffer_size + size > MAX_CHUNK_SIZE and buffer:
            flush()
            buffer = [formatted]
            buffer_size = size
        else:
            buffer.append(formatted)
            buffer_size += size

    flush()

    if split_count:
        logging.warning(f"  {split_count} oversized entries were sentence-split")
    return chunks


def write_chunks_file(chunks: List[str], path: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        for c in chunks:
            f.write(c)
            f.write('\n\n')


def create_vector_store(client: OpenAI, name: str) -> str:
    logging.info(f"Creating vector store: {name}")
    resp = client.vector_stores.create(
        name=name,
        expires_after={"anchor": "last_active_at", "days": 365},
    )
    logging.info(f"  id: {resp.id}")
    return resp.id


def upload_and_wait(client: OpenAI, file_path: str, vector_store_id: str) -> str:
    with open(file_path, 'rb') as f:
        file_resp = client.files.create(file=f, purpose="assistants")
    logging.info(f"Uploaded file: {file_resp.id}")

    client.vector_stores.files.create(
        vector_store_id=vector_store_id,
        file_id=file_resp.id,
    )

    while True:
        vsf = client.vector_stores.files.retrieve(
            vector_store_id=vector_store_id,
            file_id=file_resp.id,
        )
        if vsf.status == 'completed':
            logging.info("File processing complete")
            return file_resp.id
        if vsf.status in ('failed', 'cancelled'):
            raise RuntimeError(f"File processing {vsf.status}")
        logging.info(f"  status={vsf.status}, waiting...")
        time.sleep(5)


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up ASL Q&A vector store")
    parser.add_argument("--version", "-v", default="qa_v1",
                        help="Version label (default: qa_v1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract and chunk without uploading")
    parser.add_argument("--output", "-o", default=None,
                        help="Write chunks to this file for review")
    args = parser.parse_args()

    load_dotenv()

    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    pdf_path = (project_root.parent / "ruleschat-evals" / "rulebook" / "ASL-QA-v31.pdf").resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    entries = extract_qa_entries(str(pdf_path))
    chunks = group_entries_into_chunks(entries)

    sizes = [len(c) for c in chunks]
    logging.info(
        f"Chunk stats: count={len(chunks)} "
        f"avg={sum(sizes) // len(sizes)} min={min(sizes)} max={max(sizes)}"
    )

    if args.output:
        write_chunks_file(chunks, args.output)
        logging.info(f"Wrote chunks to {args.output}")

    if args.dry_run:
        logging.info("--- DRY RUN: first 3 chunks ---")
        for i, c in enumerate(chunks[:3]):
            print(f"\n===== CHUNK {i + 1} ({len(c)} chars) =====")
            print(c[:800])
            if len(c) > 800:
                print(f"... [+{len(c) - 800} chars]")
        return

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        organization=os.getenv("OPENAI_ORG_ID"),
        project=os.getenv("OPENAI_PROJECT_ID"),
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
        tmp_path = tmp.name
    try:
        write_chunks_file(chunks, tmp_path)
        vs_id = create_vector_store(client, f"ASL Q&A Vector Store {args.version}")
        file_id = upload_and_wait(client, tmp_path, vs_id)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    config_path = Path("responses_api_config.json")
    config = load_config(config_path)
    config.setdefault("qa_versions", {})
    config["qa_versions"][args.version] = {
        "vector_store_id": vs_id,
        "file_id": file_id,
        "pdf_path": str(pdf_path),
        "chunking_method": "qa_entry_grouped",
        "max_chunk_size": MAX_CHUNK_SIZE,
        "total_entries": len(entries),
        "total_chunks": len(chunks),
        "created_at": datetime.now().isoformat(),
    }
    config["active_qa_version"] = args.version

    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

    logging.info(f"Config updated: active_qa_version={args.version}")


if __name__ == "__main__":
    main()
