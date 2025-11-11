"""
parse_aslr.py
================

This module implements a simple parser for the *Advanced Squad Leader* rulebook
(ASLRB) that follows the hierarchical chunking strategy described in the
assistant's recommendation.  The goal of this parser is to break the PDF
into semantically meaningful chunks, each corresponding to a single rule or
sub‑rule (for example, ``A7.36`` or ``A7.36.1``).  Each chunk is stored
together with metadata that captures its position in the rule hierarchy and
location within the original document.

The parser operates by extracting plain text from a PDF using the
``PyMuPDF`` library (imported as ``fitz``).  It then scans the text for
lines that look like rule identifiers, using a regular expression to
identify the start of each rule.  For every such match, it collects the
text until the next rule identifier and stores this as a single rule
chunk.  Metadata such as the rule identifier (e.g. ``A7.36``), its
parent identifier (e.g. ``A7.36``'s parent is ``A7``), the chapter
letter, and the optional rule title are recorded.  If desired, the
parser can also further split long rules into smaller parts based on
paragraph boundaries.

Running this script as a standalone program will produce a JSON file
containing a list of chunks.  Each list element is a dictionary with
``rule_id``, ``parent_id``, ``chapter``, ``rule_title``, ``text``, and
optional ``part`` fields.  This JSON can then be ingested into a
vector store or used for retrieval augmented generation (RAG).

Usage example::

    python parse_aslr.py --pdf path/to/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf \
                         --out aslr_rules.json

Dependencies:
    - PyMuPDF (``fitz``) must be installed in your Python environment.

Note:
    The parser expects that the PDF contains lines beginning with a
    chapter letter (A–Z), followed by at least one digit, optionally
    followed by further ``.``‑separated digits (for sub‑sections).  Lines
    that match this pattern are considered the start of a new rule.
    Cross references inside parentheses like ``(A7.36)`` are ignored
    because they do not occur at the start of a line.  If your edition of
    the rulebook uses a different formatting, adjust the regular expression
    in ``RULE_PATTERN`` accordingly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from typing import Iterable, List, Optional

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "PyMuPDF is required for this script to run. Install via `pip install PyMuPDF`."
    ) from exc


# Regular expression for detecting rule identifiers at the beginning of a line.
#
# A rule identifier consists of:
#   - a single uppercase letter (chapter),
#   - one or more digits,
#   - zero or more groups of a dot followed by one or more digits.
#
# For example: A1, A1.1, A7.36, B23.2.4, etc.  We anchor the pattern at
# the start of a line using ``^``.  The look‑ahead ``(?=\s)`` ensures
# that we match only when the identifier is followed by whitespace, so
# something like ``A7.36)`` in a cross reference does not trigger a
# false positive.
RULE_PATTERN = re.compile(r"^(?P<rule>[A-Z]\d+(?:\.\d+)*)(?=\s)")


@dataclass
class RuleChunk:
    """Data class representing a chunk of the rulebook.

    Attributes:
        rule_id: The identifier of the rule (e.g. ``A7.36.1``).
        parent_id: The immediate parent of the rule in the hierarchy.  For
            example, the parent of ``A7.36.1`` is ``A7.36``, the parent of
            ``A7.36`` is ``A7``, and the parent of ``A7`` is simply ``A``.
        chapter: The chapter letter (e.g. ``A``).
        rule_title: Optional short title of the rule if present on the same
            line as the identifier.  Not all rules have a title.
        text: The text of the rule (excluding the identifier and title).
        part: Optional part number if a long rule is split into multiple
            paragraphs.
    """

    rule_id: str
    parent_id: str
    chapter: str
    rule_title: Optional[str]
    text: str
    part: Optional[int] = None
    page_numbers: Optional[List[int]] = None

    def to_dict(self) -> dict:
        """Convert the chunk to a dictionary for JSON serialization."""
        return asdict(self)


def extract_pdf_text(pdf_path: str) -> List[tuple[str, int]]:
    """Extract the plain text from a PDF, returning lines with page numbers.

    The ``PyMuPDF`` library returns a plain text representation of each
    page.  This function iterates through all pages, splits the text
    into lines, and annotates each line with the page number it came
    from.  The resulting list can be used to reconstruct the full text
    while retaining page context for each line.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        A list of tuples ``(line_text, page_number)``.  Newlines at the
        end of pages are preserved implicitly by virtue of the line
        boundaries.
    """
    logging.info("Extracting text from %s", pdf_path)
    doc = fitz.open(pdf_path)
    lines_with_page: List[tuple[str, int]] = []
    for page_num, page in enumerate(doc, start=1):
        page_text = page.get_text("text")
        page_lines = page_text.split("\n")
        for line in page_lines:
            # Preserve empty lines to maintain paragraph boundaries
            lines_with_page.append((line, page_num))
        logging.debug("Extracted %d lines from page %d", len(page_lines), page_num)
    return lines_with_page


def normalize_text(text: str) -> str:
    """Normalize whitespace and hyphenation in extracted text.

    - Replace multiple consecutive spaces with a single space.
    - Optionally fix hyphenation at line endings (e.g. ``fire pow-\ner`` -> ``fire power``).

    This function can be extended with more sophisticated heuristics.  For
    now, it simply collapses multiple spaces and removes soft hyphenation
    artifacts at line breaks.

    Args:
        text: Raw text extracted from the PDF.

    Returns:
        Normalized text.
    """
    # Remove common hyphenation across line breaks (word split at end of line)
    # This pattern matches a hyphen immediately before a newline, followed
    # by optional spaces and then the continuation of the word on the next line.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Collapse multiple spaces into one
    text = re.sub(r"\s+", " ", text)
    # Restore paragraph breaks (double newlines) by replacing occurrences
    # where two newlines may have been collapsed into one space
    text = text.replace(" \n", "\n").replace("\n ", "\n")
    return text


def split_lines_into_rules(lines_with_page: List[tuple[str, int]]) -> List[RuleChunk]:
    """Split a list of (line_text, page_number) into rule chunks.

    This function scans the provided lines for occurrences of rule identifiers
    using ``RULE_PATTERN``.  For each identifier found, it collects the
    subsequent lines until the next identifier into a chunk.  The rule title
    is taken from the remainder of the header line following the
    identifier.  Page numbers are aggregated across all lines belonging to
    the rule.

    Args:
        lines_with_page: List of tuples ``(line_text, page_number)``.

    Returns:
        A list of ``RuleChunk`` objects with ``page_numbers`` populated.
    """
    rule_positions: List[tuple[int, re.Match[str]]] = []
    for idx, (line_text, _) in enumerate(lines_with_page):
        match = RULE_PATTERN.match(line_text)
        if match:
            rule_positions.append((idx, match))

    chunks: List[RuleChunk] = []
    for i, (line_idx, match) in enumerate(rule_positions):
        rule_id = match.group("rule")
        # Determine parent: remove the last dot segment or the numeric part after the last dot
        if "." in rule_id:
            parent_id = rule_id.rsplit(".", 1)[0]
        else:
            parent_id = rule_id[0]
        chapter_letter = rule_id[0]
        # Extract title from the header line
        header_line_text = lines_with_page[line_idx][0]
        remainder = header_line_text[match.end():].strip()
        rule_title: Optional[str] = remainder if remainder else None
        # Determine the range of lines belonging to this rule
        start_line = line_idx + 1
        end_line = (
            rule_positions[i + 1][0] if i + 1 < len(rule_positions) else len(lines_with_page)
        )
        # Gather text lines and page numbers
        text_lines: List[str] = []
        pages: set[int] = set()
        for lnum in range(start_line, end_line):
            line_text, page_num = lines_with_page[lnum]
            text_lines.append(line_text)
            pages.add(page_num)
        rule_text = "\n".join(text_lines).strip()
        # Normalize rule text (collapse spaces, fix hyphenation)
        # We perform normalization at the rule level to avoid breaking line/page mapping
        rule_text_norm = normalize_text(rule_text)
        # If the rule text is long, split on double newlines into parts
        paragraphs = [p.strip() for p in rule_text_norm.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            chunks.append(
                RuleChunk(
                    rule_id=rule_id,
                    parent_id=parent_id,
                    chapter=chapter_letter,
                    rule_title=rule_title,
                    text=rule_text_norm,
                    part=None,
                    page_numbers=sorted(pages) if pages else None,
                )
            )
        else:
            for part_idx, para in enumerate(paragraphs, start=1):
                chunks.append(
                    RuleChunk(
                        rule_id=rule_id,
                        parent_id=parent_id,
                        chapter=chapter_letter,
                        rule_title=rule_title if part_idx == 1 else None,
                        text=para,
                        part=part_idx,
                        page_numbers=sorted(pages) if pages else None,
                    )
                )
    return chunks


def parse_aslr_rulebook(pdf_path: str, max_parts: int = 0) -> List[RuleChunk]:
    """Parse the ASL rulebook PDF into a list of rule chunks.

    This function runs the complete pipeline: it extracts lines along with
    page numbers, identifies rule headers, collects the text of each rule
    (with optional paragraph splitting), and returns a list of
    ``RuleChunk`` objects.  The ``max_parts`` argument can be used to
    limit the number of paragraphs stored for each rule; any remaining
    text is merged into the last part.  This can be useful when building
    a vector store with shorter chunks.

    Args:
        pdf_path: Path to the ASLRB PDF.
        max_parts: If greater than zero, limit the number of paragraph
            parts per rule.  A value of zero keeps all parts.

    Returns:
        A list of ``RuleChunk`` objects with page numbers.
    """
    # Extract raw lines with page context
    lines_with_page = extract_pdf_text(pdf_path)
    # Split into rule chunks
    all_chunks = split_lines_into_rules(lines_with_page)
    # Optionally limit the number of parts per rule
    if max_parts > 0:
        limited_chunks: List[RuleChunk] = []
        current_rule = None
        parts_seen = 0
        for chunk in all_chunks:
            if current_rule != chunk.rule_id:
                current_rule = chunk.rule_id
                parts_seen = 1
            else:
                parts_seen += 1
            if parts_seen <= max_parts:
                limited_chunks.append(chunk)
            else:
                # Append overflow text to the last kept chunk for this rule
                prev = limited_chunks[-1]
                prev.text += "\n\n" + chunk.text
        return limited_chunks
    return all_chunks


def save_chunks_to_json(chunks: Iterable[RuleChunk], out_path: str) -> None:
    """Save rule chunks to a JSON file.

    Args:
        chunks: Iterable of ``RuleChunk`` objects.
        out_path: Path to the output JSON file.
    """
    data = [chunk.to_dict() for chunk in chunks]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logging.info("Wrote %d chunks to %s", len(data), out_path)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Parse the ASL rulebook PDF into structured rule chunks",
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the PDF file (e.g., eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to the output JSON file to write rule chunks to",
    )
    parser.add_argument(
        "--max-parts",
        type=int,
        default=0,
        help=(
            "If greater than zero, limit the number of paragraph parts per rule "
            "(useful for long sections); overflow text is merged into the last part."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if not os.path.isfile(args.pdf):
        parser.error(f"PDF file not found: {args.pdf}")
    chunks = parse_aslr_rulebook(args.pdf, max_parts=args.max_parts)
    save_chunks_to_json(chunks, args.out)


if __name__ == "__main__":
    main()