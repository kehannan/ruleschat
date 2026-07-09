"""
Deterministic cite-check: ground a drafted answer in the exact text of the
sections it cites.

This is the reliability backbone for the gpt-5.4-and-cheaper tier
(docs/agentic_retrieval_plan.md §3.4 / T7). CODE — not the model — extracts
the cited section IDs from a draft answer, fetches their exact text (plus one
level of cross-references, plus official Q&A/errata) via app.asl.rules_lookup,
and the service then forces ONE revision turn with that context. Nothing here
depends on the model choosing to call tools.

Pure functions, no service state; the service owns the model call.
"""
import re
from typing import Any, Dict, List, Optional

from app.asl import rules_lookup

# Section-ID shaped tokens: A7.302, B27.1, W10.44, G13.732, also bare "A7".
# The valid-ID filter (against the extracted store's keys) is what kills false
# positives — hex IDs like "H8" in "57-H8", scenario names, unit designations.
SECTION_ID_RE = re.compile(r"\b[A-Z]{1,2}\d+(?:\.\d+)*\b")

MAX_SECTIONS_DEFAULT = 12
MAX_CHARS_DEFAULT = 16000


def extract_section_ids(text: str, valid_ids: set) -> List[str]:
    """Ordered, deduped section IDs appearing in `text` that are real sections."""
    seen: Dict[str, None] = {}
    for m in SECTION_ID_RE.finditer(text or ""):
        tok = m.group(0)
        if tok in valid_ids and tok not in seen:
            seen[tok] = None
    return list(seen)


def _format_block(sec: Dict[str, Any]) -> str:
    """One section's revision-context block: rule text + Q&A/errata."""
    parts = [f"### {sec['section']} (rulebook p{sec.get('page', '?')})"]
    if sec.get("note"):
        parts.append(f"[note: {sec['note']}]")
    parts.append(sec.get("text") or "")
    for qa in sec.get("qa", []) or []:
        parts.append(f"[{qa.get('kind', 'qa')}] {qa.get('text', '')}")
    return "\n".join(p for p in parts if p)


def build_cite_check_context(
    draft: str,
    max_sections: int = MAX_SECTIONS_DEFAULT,
    max_chars: int = MAX_CHARS_DEFAULT,
) -> Dict[str, Any]:
    """Collect the exact texts backing a draft answer's citations.

    Returns:
        {
          "sections": {id: formatted block},   # insertion order = priority
          "cited": [...],                      # IDs found in the draft
          "cross_refs": [...],                 # IDs pulled in via expansion
          "dropped": [...],                    # over-cap casualties
          "missing": [...],                    # IDs the store couldn't resolve
        }
    An empty "sections" means the draft cited nothing valid (caller should
    skip the revision turn and log).
    """
    valid = rules_lookup.valid_section_ids()
    cited = extract_section_ids(draft, valid)

    sections: Dict[str, str] = {}
    missing: List[str] = []
    fetched: Dict[str, Dict[str, Any]] = {}

    def fetch(sec_id: str) -> Optional[Dict[str, Any]]:
        if sec_id in fetched:
            return fetched[sec_id]
        r = rules_lookup.get_section(sec_id, include_qa=True)
        if "error" in r:
            missing.append(sec_id)
            return None
        fetched[sec_id] = r
        return r

    # Level 1: sections the draft cites, in citation order.
    level1 = [r for sid in cited if (r := fetch(sid)) is not None]

    # Level 2: sections cross-referenced INSIDE the fetched rule texts (one
    # level only; rule text, not Q&A — Q&A quotes many tangential sections).
    cross_ref_ids: List[str] = []
    already = set(cited)
    for r in level1:
        for ref in extract_section_ids(r.get("text") or "", valid):
            if ref not in already:
                already.add(ref)
                cross_ref_ids.append(ref)

    dropped: List[str] = []
    used_chars = 0

    def try_add(r: Dict[str, Any]) -> None:
        nonlocal used_chars
        key = r["section"]
        if key in sections:
            return
        block = _format_block(r)
        if len(sections) >= max_sections or used_chars + len(block) > max_chars:
            dropped.append(key)
            return
        sections[key] = block
        used_chars += len(block)

    for r in level1:
        try_add(r)
    for ref in cross_ref_ids:
        r = fetch(ref)
        if r is not None:
            try_add(r)

    return {
        "sections": sections,
        "cited": cited,
        "cross_refs": [c for c in cross_ref_ids if c in sections],
        "dropped": dropped,
        "missing": missing,
    }


REVISION_PROMPT_TEMPLATE = """CITATION VERIFICATION PASS

A draft answer to an ASL rules question is below, followed by the EXACT rulebook text of every section it cites (plus sections those texts cross-reference, and official Q&A/errata entries). The fetched texts are authoritative.

Revise the draft ONLY if:
- a fetched text contains a qualifier, exception (EXC:), "unless", or NA clause that contradicts it; or
- a numeric value the draft uses (TEM, DRM, FP, MF/MP cost, morale level, limit) disagrees with a fetched text; or
- an official Q&A/errata entry rules the situation differently.

If nothing contradicts the draft, return it UNCHANGED. Keep the same style and keep all rule citations. Do not mention this verification pass. Output only the final answer.

QUESTION:
{question}

DRAFT ANSWER:
{draft}

FETCHED RULE TEXTS:
{blocks}"""


def build_revision_prompt(question: str, draft: str, context: Dict[str, Any]) -> str:
    blocks = "\n\n".join(context["sections"].values())
    return REVISION_PROMPT_TEMPLATE.format(question=question, draft=draft, blocks=blocks)
