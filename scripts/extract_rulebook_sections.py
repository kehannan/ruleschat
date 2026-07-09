#!/usr/bin/env python3
"""
Extract per-section rule text from the eASLRB PDF into data/rulebook/sections.json.

Uses static/rulebook/section_pages.json (2,781 section IDs -> 1-based PDF page)
as the authoritative index: for each section, locate its printed heading on its
known page and slice text from there to the next section's heading. The known-
next-section anchor is what makes the slicing reliable — no freeform structure
inference.

Quirks of the eASLRB this handles (verified against the PDF, 2026-07-09):
  * Printed headings OMIT the chapter letter: section "A6.21" appears on the
    page as "6.21 HALF-LEVEL OBSTACLES:". The letter exists only in page
    headers and the index.
  * Two-column layout: naive extract_text() interleaves the columns line by
    line. Pages are cropped into left/right halves and read column-by-column.
  * Some headings lose the space to the following word ("7.224If ...") and
    some titles are sentence-case, not ALL-CAPS ("4.4 Movement costs ...").
  * Page-header/footer artifacts (bare chapter letter, bare section number,
    bare page number on their own line) are stripped.
  * A few index entries are phantoms (e.g. "A7.30" — no such printed section;
    the real parent is A7.3). These are reported as misses; the runtime lookup
    falls back to the parent section.

Output is GITIGNORED (copyrighted rulebook text — never commit it).

Usage:
    python scripts/extract_rulebook_sections.py            # full build + coverage report
    python scripts/extract_rulebook_sections.py --show A12.14
    python scripts/extract_rulebook_sections.py --pages 44-70   # subset (debugging)
"""
import argparse
import json
import re
import sys
import time
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")  # pdfplumber color-space noise on this PDF

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = REPO_ROOT / "static" / "rulebook" / "eASLRB_v3_14_INHERIT_ZOOM.pdf"
INDEX_PATH = REPO_ROOT / "static" / "rulebook" / "section_pages.json"
OUT_PATH = REPO_ROOT / "data" / "rulebook" / "sections.json"

# Lines that are page furniture, not content: bare chapter letter, bare dotted
# section number (running header), bare integer (page number).
_JUNK_LINE = re.compile(r"^(?:[A-Z]{1,2}|\d+\.\d+|\d{1,3})$")


def numeric_id(section: str) -> str:
    """A6.21 -> 6.21 (printed headings omit the chapter letter)."""
    return re.sub(r"^[A-Z]+", "", section)


def clean_lines(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not _JUNK_LINE.match(ln.strip()))


def page_column_text(page, top_min: float = None, top_max: float = None) -> str:
    """Extract a page reading left column fully, then right column.

    Word-based reconstruction instead of bbox cropping: the column gutter
    position varies per page in this PDF (some pages have a ~48pt mediabox
    x-offset, some don't), so a fixed midline slices through headings that
    start near it. Detect the midpoint from the page's actual content range
    and assign whole words to a column — a word is never split.

    top_min/top_max drop words outside a vertical band (used by the Q&A
    extractor to strip running headers/footers).
    """
    try:
        words = page.extract_words()
    except Exception:
        words = []
    if top_min is not None:
        words = [w for w in words if w["top"] >= top_min]
    if top_max is not None:
        words = [w for w in words if w["top"] <= top_max]
    if not words:
        return ""
    cx0 = min(w["x0"] for w in words)
    cx1 = max(w["x1"] for w in words)
    mid = (cx0 + cx1) / 2
    cols = ([], [])
    for w in words:
        cols[0 if w["x0"] < mid - 2 else 1].append(w)

    def rebuild(ws):
        ws.sort(key=lambda w: (round(w["top"]), w["x0"]))
        lines, cur, cur_top = [], [], None
        for w in ws:
            if cur_top is None or abs(w["top"] - cur_top) <= 2.5:
                cur.append(w["text"])
                cur_top = w["top"] if cur_top is None else cur_top
            else:
                lines.append(" ".join(cur))
                cur, cur_top = [w["text"]], w["top"]
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)

    return clean_lines(rebuild(cols[0]) + "\n" + rebuild(cols[1]))


def heading_candidates(num: str, text: str):
    """Yield (score, position) for plausible heading occurrences of `num`.

    Score: 3 = followed by an ALL-CAPS title word; 2 = at line start followed
    by a capital (covers sentence-case and glued titles); 1 = mid-line followed
    by a capital letter (column-merge leftovers). Occurrences preceded by an
    opening paren or a cross-reference word are rejected.
    """
    esc = re.escape(num)
    for m in re.finditer(rf"(?<![\d.]){esc}(?!\d)", text):
        s, e = m.start(), m.end()
        before = text[max(0, s - 6):s]
        if before.endswith("(") or re.search(r"(?:per|see|EXC:)\s*$", before):
            continue
        after = text[e:e + 40]
        line_start = s == 0 or text[s - 1] == "\n"
        # ALL-CAPS-ish title (allows #KIA:, A-T, MG, "360o MOUNT"), possibly
        # glued to the number; an em-dash "title" ("7.306 —: No Effect"); or a
        # bracketed exception body ("12.31 [EXC: ...")
        if (re.match(r"[\s.]{0,2}[#\d]?[A-Z][A-Z&\-/]{1,}", after)
                or re.match(r"\s\d{1,3}[a-z]?\s?[A-Z][A-Z&\-/]{1,}", after)
                or re.match(r"\s?[—–]", after)
                or re.match(r"\s?\[", after)):
            yield (3, s)
        elif line_start and re.match(r"\s?\(?[#A-Za-z“\"]", after):
            yield (2, s)
        elif re.match(r"\s?[#A-Z]", after):
            yield (1, s)
        elif re.match(r"\s?\n", after):
            # Heading number sheared from its title by an embedded example/
            # figure box (y-overlap in extraction). Positionally still the
            # right slice point; last resort only.
            yield (1, s)


def find_heading(num: str, text: str):
    """Best heading position of `num` in `text`, or None."""
    best = None
    for score, pos in heading_candidates(num, text):
        if best is None or score > best[0] or (score == best[0] and pos < best[1]):
            best = (score, pos)
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", metavar="SECTION", help="print one extracted section and exit (reads existing output)")
    ap.add_argument("--pages", metavar="A-B", help="only process index entries in this page range (debugging)")
    ap.add_argument("--miss-limit", type=int, default=50, help="how many miss IDs to list in the report")
    args = ap.parse_args()

    if args.show and not args.pages:
        data = json.load(open(OUT_PATH))
        entry = data["sections"].get(args.show.upper())
        if not entry:
            sys.exit(f"{args.show}: not in {OUT_PATH}")
        print(f"=== {args.show} (page {entry['page']}) ===")
        print(entry["text"] if entry["text"] else "<MISS — heading not found during extraction>")
        return

    index = json.load(open(INDEX_PATH))
    if args.pages:
        lo, hi = (int(x) for x in args.pages.split("-"))
        index = {k: v for k, v in index.items() if lo <= v <= hi}

    t0 = time.time()
    needed_pages = sorted(set(index.values()))
    page_texts = {}   # 1-based page -> cleaned column-ordered text
    with pdfplumber.open(PDF_PATH) as pdf:
        n_pages = len(pdf.pages)
        # A section's text can spill past its own page, so we need every page
        # from the first indexed one to the last (+1 for the final section).
        first, last = needed_pages[0], min(needed_pages[-1] + 1, n_pages)
        for p in range(first, last + 1):
            page_texts[p] = page_column_text(pdf.pages[p - 1])
            if (p - first) % 100 == 0:
                print(f"  ...extracted page {p}/{last} ({time.time() - t0:.0f}s)", file=sys.stderr)

    # Global stream: pages in order, with per-page offsets, so a slice can run
    # across page boundaries.
    offsets, stream_parts, cursor = {}, [], 0
    for p in sorted(page_texts):
        offsets[p] = cursor
        stream_parts.append(page_texts[p])
        cursor += len(page_texts[p]) + 1  # +1 for the join "\n"
    stream = "\n".join(stream_parts)

    # Locate every heading on its indexed page.
    located, misses = [], []
    for sec, page in index.items():
        pos = find_heading(numeric_id(sec), page_texts.get(page, ""))
        if pos is None:
            misses.append(sec)
        else:
            located.append((offsets[page] + pos, sec, page))
    located.sort()

    # Slice between consecutive located headings.
    sections = {}
    for i, (gpos, sec, page) in enumerate(located):
        end = located[i + 1][0] if i + 1 < len(located) else min(len(stream), gpos + 6000)
        sections[sec] = {"text": stream[gpos:end].strip(), "page": page}
    for sec in misses:
        sections[sec] = {"text": None, "page": index[sec]}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "source_pdf": PDF_PATH.name,
        "built": date.today().isoformat(),
        "sections": len(index),
        "extracted": len(located),
        "misses": len(misses),
    }
    json.dump({"meta": meta, "sections": sections}, open(OUT_PATH, "w"), ensure_ascii=False)

    cov = 100.0 * len(located) / max(1, len(index))
    print(f"\n== coverage report ==")
    print(f"index entries : {len(index)}")
    print(f"extracted     : {len(located)}  ({cov:.1f}%)")
    print(f"misses        : {len(misses)}")
    if misses:
        print(f"first {min(args.miss_limit, len(misses))} miss IDs: {sorted(misses)[:args.miss_limit]}")
    print(f"output        : {OUT_PATH}  ({OUT_PATH.stat().st_size / 1e6:.1f} MB, gitignored)")
    print(f"elapsed       : {time.time() - t0:.0f}s")
    if cov < 95.0 and not args.pages:
        print("!! coverage below the 95% acceptance bar", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
