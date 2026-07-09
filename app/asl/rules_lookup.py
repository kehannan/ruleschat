"""
Deterministic rule-section lookup over the extracted rulebook + Q&A stores.

Backs the `get_section` agentic tool and the cite-check pass. Reads the
gitignored stores built by scripts/extract_rulebook_sections.py and
scripts/extract_qa_entries.py:

    data/rulebook/sections.json    {"meta": ..., "sections": {"A12.14": {"text", "page"}}}
    data/rulebook/qa_entries.json  {"meta": ..., "by_section": {"A12.14": [{...}]}}

Design constraints (docs/agentic_retrieval_plan.md T2):
  * Never raises on a missing/unbuilt store — returns a clean {"error": ...}
    (sections) or degrades to no Q&A (qa store is optional).
  * A requested section that doesn't exist (or extracted empty) falls back to
    its nearest existing ancestor (A12.147 -> A12.14 -> A12.1 -> A12) with a
    "note" saying so; if no ancestor exists, returns "did_you_mean" hints.
  * Output is tool-facing: capped sizes, plain dicts, JSON-serializable.
"""
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECTIONS_PATH = _REPO_ROOT / "data" / "rulebook" / "sections.json"
DEFAULT_QA_PATH = _REPO_ROOT / "data" / "rulebook" / "qa_entries.json"

SUBSECTIONS_CHAR_CAP = 4000
QA_ENTRY_CAP = 5
QA_CHAR_CAP = 4000

_lock = threading.Lock()
_sections: Optional[Dict[str, Dict[str, Any]]] = None   # None = not loaded
_qa: Optional[Dict[str, List[Dict[str, Any]]]] = None
_sections_path_loaded: Optional[str] = None
_qa_path_loaded: Optional[str] = None

NOT_BUILT_ERROR = (
    "section database not built; run scripts/extract_rulebook_sections.py"
)


def load_sections(path: Optional[str] = None, qa_path: Optional[str] = None) -> None:
    """(Re)load the stores. Called lazily by get_section; call explicitly in
    tests to point at fixtures. Missing files load as empty (handled at query
    time), never raise."""
    global _sections, _qa, _sections_path_loaded, _qa_path_loaded
    spath = str(path or DEFAULT_SECTIONS_PATH)
    qpath = str(qa_path or DEFAULT_QA_PATH)
    with _lock:
        try:
            _sections = json.load(open(spath))["sections"]
        except (OSError, ValueError, KeyError) as e:
            logging.warning("rules_lookup: sections store unavailable (%s): %s", spath, e)
            _sections = {}
        try:
            _qa = json.load(open(qpath))["by_section"]
        except (OSError, ValueError, KeyError) as e:
            logging.info("rules_lookup: Q&A store unavailable (%s): %s", qpath, e)
            _qa = {}
        _sections_path_loaded, _qa_path_loaded = spath, qpath


def _ensure_loaded() -> None:
    if _sections is None:
        load_sections()


def reset() -> None:
    """Drop loaded stores (tests)."""
    global _sections, _qa, _sections_path_loaded, _qa_path_loaded
    with _lock:
        _sections = _qa = None
        _sections_path_loaded = _qa_path_loaded = None


def valid_section_ids() -> set:
    """The set of known section IDs (empty if the store isn't built)."""
    _ensure_loaded()
    return set(_sections or {})


def normalize_section_id(section: str) -> str:
    """'a12.14.' -> 'A12.14'."""
    s = (section or "").strip().rstrip(".").replace(" ", "")
    m = re.match(r"^([A-Za-z]{1,2})(\d.*)$", s)
    if m:
        return m.group(1).upper() + m.group(2)
    return s.upper()


def _ancestors(section: str) -> List[str]:
    """A12.147 -> [A12.14, A12.1, A12]. Walks the dotted-decimal hierarchy:
    trim one trailing digit from the fraction at a time, then drop the dot."""
    out = []
    m = re.match(r"^([A-Z]{1,2}\d+)\.(\d+)$", section)
    if not m:
        return out
    stem, frac = m.group(1), m.group(2)
    while len(frac) > 1:
        frac = frac[:-1]
        out.append(f"{stem}.{frac}")
    out.append(stem)
    return out


def _did_you_mean(section: str, limit: int = 5) -> List[str]:
    """Nearby known IDs sharing the longest prefix with the request."""
    keys = _sections or {}
    for cut in range(len(section), 0, -1):
        prefix = section[:cut]
        hits = sorted(k for k in keys if k.startswith(prefix))
        if hits:
            return hits[:limit]
    return []


def _children(section: str) -> List[str]:
    """Direct children one dotted level deeper: A12.14 -> A12.141, A12.142."""
    keys = _sections or {}
    m = re.match(r"^([A-Z]{1,2}\d+)(?:\.(\d+))?$", section)
    if not m:
        return []
    stem, frac = m.group(1), m.group(2)
    if frac:
        pat = re.compile(rf"^{re.escape(stem)}\.{re.escape(frac)}\d$")
    else:
        pat = re.compile(rf"^{re.escape(stem)}\.\d$")
    return sorted(k for k in keys if pat.match(k))


def _qa_for(section: str) -> Dict[str, Any]:
    entries = (_qa or {}).get(section, [])
    out, used, truncated = [], 0, False
    for e in entries:
        if len(out) >= QA_ENTRY_CAP or used + len(e.get("text", "")) > QA_CHAR_CAP:
            truncated = True
            break
        out.append({"text": e.get("text", ""), "kind": e.get("kind", "")})
        used += len(e.get("text", ""))
    result: Dict[str, Any] = {"qa": out}
    if truncated:
        result["qa_truncated"] = True
    return result


def get_section(
    section: str,
    include_subsections: bool = False,
    include_qa: bool = True,
) -> Dict[str, Any]:
    """Fetch the exact text of a rule section (plus optional children and Q&A).

    Returns a JSON-serializable dict:
      hit  -> {"section", "text", "page", ["subsections"], ["truncated"],
               ["qa"], ["qa_truncated"], ["note"]}
      miss -> {"error", ["did_you_mean"]}
    Never raises on bad input or missing stores.
    """
    _ensure_loaded()
    if not _sections:
        return {"error": NOT_BUILT_ERROR}

    requested = normalize_section_id(section)
    if not requested:
        return {"error": "empty section id"}

    resolved, note = requested, None
    entry = _sections.get(resolved)
    if entry is None or not entry.get("text"):
        # walk up to the nearest ancestor that has text
        for anc in _ancestors(requested):
            e = _sections.get(anc)
            if e is not None and e.get("text"):
                resolved, entry = anc, e
                note = f"requested {requested} not found; returning parent {anc}"
                break
        else:
            hints = _did_you_mean(requested)
            out: Dict[str, Any] = {"error": f"section {requested} not found"}
            if hints:
                out["did_you_mean"] = hints
            return out

    result: Dict[str, Any] = {
        "section": resolved,
        "text": entry["text"],
        "page": entry.get("page"),
    }
    if note:
        result["note"] = note

    if include_subsections:
        subs, used, truncated = [], 0, False
        for child in _children(resolved):
            ctext = (_sections.get(child) or {}).get("text") or ""
            if used + len(ctext) > SUBSECTIONS_CHAR_CAP:
                truncated = True
                break
            subs.append({"section": child, "text": ctext})
            used += len(ctext)
        result["subsections"] = subs
        if truncated:
            result["truncated"] = True

    if include_qa:
        result.update(_qa_for(resolved))

    return result
