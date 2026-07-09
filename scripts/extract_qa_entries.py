#!/usr/bin/env python3
"""
Extract Q&A / errata entries from ASL-QA-v31.pdf into data/rulebook/qa_entries.json.

The compilation (Scott Romanowski's "Questions & Answers, Clarifications, &
Errata") keys every rules entry to one or more section IDs at line start:

    A7.51 & D6.64 Can LVT Passengers fire ... A. Yes. [BRTG; Mw24H]

Entries are grouped by the running page header into three kinds we keep —
"Official Q&A: Rules" -> official-qa, "Unofficial Q&A: Rules" -> unofficial-qa,
"Errata: ASL Rulebook" -> errata — and everything else (contents, scenario /
module Q&A "Other Items", sources) is skipped. Entry-start section IDs are
validated against static/rulebook/section_pages.json, which kills false
positives from cross-references and scenario names.

Output is GITIGNORED (third-party content — never commit it).

Usage:
    python scripts/extract_qa_entries.py               # full build + report
    python scripts/extract_qa_entries.py --show A7.53
"""
import argparse
import json
import re
import sys
import time
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_rulebook_sections import page_column_text  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = REPO_ROOT / "static" / "rulebook" / "ASL-QA-v31.pdf"
INDEX_PATH = REPO_ROOT / "static" / "rulebook" / "section_pages.json"
OUT_PATH = REPO_ROOT / "data" / "rulebook" / "qa_entries.json"

# Running header band (measured): header words sit at top≈23, body starts ≈51,
# a footer line sits at ≈761 on some pages.
BODY_TOP, BODY_BOTTOM = 40.0, 755.0

KIND_MARKERS = [
    ("Official Q&A: Rules", "official-qa"),
    ("Unofficial Q&A: Rules", "unofficial-qa"),
    ("Errata: ASL Rulebook", "errata"),
]

# One section ID, e.g. A7.51, D6.64, W10.44; optionally a range suffix as the
# compilation writes it: "A7.53-.531" (keyed under the base ID).
_ID = r"[A-Z]{1,2}\d+(?:\.\d+)*"
_ID_TOKEN = rf"{_ID}(?:[-–]\.?\d+(?:\.\d+)*)?"
# An entry opens with an ID chain at line start — "A7.51", "A14.21 & B6.3",
# "A7.53, A10.7 & D6.65" — then the question/errata text, which starts with a
# capital / quote / paren (questions: "Can/If/Does/See...", errata: "In line
# 2...", "Add ...").
_ENTRY_START = re.compile(
    rf"(?m)^(?P<ids>{_ID_TOKEN}(?:\s*[,&]\s*{_ID_TOKEN})*)\s+(?=[A-Z“\"(\[])"
)


def page_kind(page) -> str:
    """Classify a page by its running header; '' means skip the page."""
    try:
        words = page.extract_words()
    except Exception:
        return ""
    header = " ".join(w["text"] for w in words if w["top"] < BODY_TOP)
    for marker, kind in KIND_MARKERS:
        if marker in header:
            return kind
    return ""


def parse_run(text: str, offset_to_page, valid_ids) -> list:
    """Parse one contiguous same-kind text run into entries.

    offset_to_page: callable mapping a char offset in `text` to its PDF page.
    """
    starts = []
    for m in _ENTRY_START.finditer(text):
        tokens = [i.strip() for i in re.split(r"\s*[,&]\s*", m.group("ids"))]
        # "A7.53-.531" -> base id A7.53
        bases = [re.match(_ID, t).group(0) for t in tokens if re.match(_ID, t)]
        valid = list(dict.fromkeys(i for i in bases if i in valid_ids))
        if not valid:
            continue  # scenario name / cross-ref artifact / unknown ID
        starts.append((m.start(), valid))
    entries = []
    for i, (pos, ids) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        body = re.sub(r"\s*\n\s*", " ", text[pos:end]).strip()
        if len(body) < 8:
            continue
        entries.append({
            "sections": ids,
            "text": body,
            "page": offset_to_page(pos),
        })
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", metavar="SECTION", help="print entries for one section from existing output")
    ap.add_argument("--long-limit", type=int, default=2500, help="report entries longer than this")
    args = ap.parse_args()

    if args.show:
        data = json.load(open(OUT_PATH))
        hits = data["by_section"].get(args.show.upper(), [])
        print(f"=== {args.show}: {len(hits)} entr{'y' if len(hits) == 1 else 'ies'} ===")
        for e in hits:
            print(f"\n[{e['kind']} · p{e['page']} · keys {'/'.join(e['sections'])}]")
            print(e["text"])
        return

    valid_ids = set(json.load(open(INDEX_PATH)))
    t0 = time.time()

    # Group contiguous same-kind pages into runs, then parse each run as one
    # stream so entries can span page boundaries.
    all_entries = []
    with pdfplumber.open(PDF_PATH) as pdf:
        run_kind, run_parts, run_page_bounds = None, [], []  # parts: per-page text

        def flush_run():
            if not run_parts:
                return
            text = "\n".join(run_parts)
            bounds = []
            cursor = 0
            for pg, part in zip(run_page_bounds, run_parts):
                bounds.append((cursor, pg))
                cursor += len(part) + 1
            def offset_to_page(pos):
                page = bounds[0][1]
                for start, pg in bounds:
                    if pos >= start:
                        page = pg
                    else:
                        break
                return page
            for e in parse_run(text, offset_to_page, valid_ids):
                e["kind"] = run_kind
                all_entries.append(e)

        for pno, page in enumerate(pdf.pages, 1):
            kind = page_kind(page)
            if kind != run_kind:
                flush_run()
                run_kind, run_parts, run_page_bounds = kind, [], []
            if kind:
                run_parts.append(page_column_text(page, top_min=BODY_TOP, top_max=BODY_BOTTOM))
                run_page_bounds.append(pno)
            if pno % 50 == 0:
                print(f"  ...page {pno}/{len(pdf.pages)} ({time.time() - t0:.0f}s)", file=sys.stderr)
        flush_run()

    # Build the by_section map (entry duplicated under each of its keys).
    by_section = {}
    for e in all_entries:
        for sec in e["sections"]:
            by_section.setdefault(sec, []).append(e)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    from collections import Counter
    kind_counts = Counter(e["kind"] for e in all_entries)
    meta = {
        "source_pdf": PDF_PATH.name,
        "built": date.today().isoformat(),
        "entries": len(all_entries),
        "by_kind": dict(kind_counts),
        "sections_covered": len(by_section),
    }
    json.dump({"meta": meta, "by_section": by_section}, open(OUT_PATH, "w"), ensure_ascii=False)

    too_long = [e for e in all_entries if len(e["text"]) > args.long_limit]
    print("\n== Q&A extraction report ==")
    print(f"entries parsed    : {len(all_entries)}  {dict(kind_counts)}")
    print(f"sections covered  : {len(by_section)}")
    print(f"entries > {args.long_limit} chars: {len(too_long)}")
    for e in too_long[:10]:
        print(f"   {'/'.join(e['sections'])} (p{e['page']}, {len(e['text'])} chars): {e['text'][:80]}...")
    print(f"output            : {OUT_PATH}  ({OUT_PATH.stat().st_size / 1e6:.1f} MB, gitignored)")
    print(f"elapsed           : {time.time() - t0:.0f}s")
    if len(all_entries) < 500:
        print("!! below the 500-entry sanity floor", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
