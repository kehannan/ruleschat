#!/usr/bin/env python
"""
Tests for app.asl.cite_check (deterministic cite-check context builder).

Runs against the committed invented-text fixtures — never the real stores.
Runnable directly (`python tests/test_cite_check.py`) or under pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import rules_lookup
from app.asl.cite_check import (
    extract_section_ids,
    build_cite_check_context,
    build_revision_prompt,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fresh():
    rules_lookup.reset()
    rules_lookup.load_sections(
        str(FIXTURES / "rulebook_sections_fixture.json"),
        str(FIXTURES / "qa_entries_fixture.json"),
    )


VALID = {"Z1", "Z1.1", "Z1.11", "Z2.1", "A7.302", "B27.1"}


def test_extract_basic_and_order():
    ids = extract_section_ids("Per Z1.11 and B27.1, then Z1.11 again.", VALID)
    assert ids == ["Z1.11", "B27.1"], ids


def test_extract_rejects_invalid_and_hexids():
    text = "Unit in 57-H8 fires; see H8 and Q9.99 and A7.302."
    ids = extract_section_ids(text, VALID)
    assert ids == ["A7.302"], ids  # H8/Q9.99 not real sections


def test_extract_ignores_lowercase_and_decimals():
    ids = extract_section_ids("roll 3.5 average, za1.1 z1.11", VALID)
    assert ids == [], ids


def test_context_fetches_cited_and_cross_refs():
    _fresh()
    # Z1.11 text cross-references Z2.1 ("unless it is Elite (Z2.1)")
    ctx = build_cite_check_context("The answer is governed by Z1.11.")
    assert "Z1.11" in ctx["sections"]
    assert "Z2.1" in ctx["sections"], "cross-referenced section should be pulled in"
    assert ctx["cited"] == ["Z1.11"]
    assert "Z2.1" in ctx["cross_refs"]
    assert ctx["missing"] == []


def test_context_includes_qa_entries():
    _fresh()
    ctx = build_cite_check_context("See Z1.1 for activation.")
    assert "FIX1" in ctx["sections"]["Z1.1"], "Q&A entries must be in the block"


def test_context_empty_for_uncited_draft():
    _fresh()
    ctx = build_cite_check_context("No sections are mentioned here at all.")
    assert ctx["sections"] == {} and ctx["cited"] == []


def test_context_caps_sections():
    _fresh()
    ctx = build_cite_check_context("Z1, Z1.1, Z1.11, Z1.12, Z1.2, Z2, Z2.1 all apply.",
                                   max_sections=3)
    assert len(ctx["sections"]) == 3
    assert ctx["dropped"], "over-cap sections must be reported as dropped"
    # citation order wins: first three cited IDs kept
    assert list(ctx["sections"]) == ["Z1", "Z1.1", "Z1.11"]


def test_context_caps_chars():
    _fresh()
    ctx = build_cite_check_context("Z1 and Z1.1 and Z1.11.", max_chars=150)
    assert len(ctx["sections"]) >= 1
    assert ctx["dropped"]


def test_missing_section_reported_not_fatal():
    _fresh()
    # Z3 exists in the store but has null text -> get_section errors for it...
    # except parent fallback: Z3 has no dotted parent, so it's a miss.
    ctx = build_cite_check_context("Z3 and Z1.1 both apply.")
    # NB: Z3 is only 'cited' if in valid ids — it IS a key in the store.
    assert "Z1.1" in ctx["sections"]
    assert "Z3" in ctx["missing"]


def test_revision_prompt_contains_everything():
    _fresh()
    draft = "Per Z1.11, a widget may activate twice."
    ctx = build_cite_check_context(draft)
    prompt = build_revision_prompt("Can widgets double-activate?", draft, ctx)
    assert "Can widgets double-activate?" in prompt
    assert draft in prompt
    assert "DOUBLE ACTIVATION" in prompt
    assert "Output only the final answer." in prompt


def test_store_not_built_degrades_to_empty():
    rules_lookup.reset()
    rules_lookup.load_sections("/nonexistent/sections.json", "/nonexistent/qa.json")
    ctx = build_cite_check_context("Per A12.14 the unit loses concealment.")
    assert ctx["sections"] == {} and ctx["cited"] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
