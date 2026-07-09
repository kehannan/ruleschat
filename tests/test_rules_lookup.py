#!/usr/bin/env python
"""
Tests for app.asl.rules_lookup (the get_section backing store).

Runs entirely against committed fixtures with invented rule text — never the
real (gitignored) stores. Runnable directly (`python tests/test_rules_lookup.py`)
or under pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import rules_lookup

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECTIONS = str(FIXTURES / "rulebook_sections_fixture.json")
QA = str(FIXTURES / "qa_entries_fixture.json")


def _fresh():
    rules_lookup.reset()
    rules_lookup.load_sections(SECTIONS, QA)


def test_exact_hit():
    _fresh()
    r = rules_lookup.get_section("Z1.11")
    assert r["section"] == "Z1.11" and "DOUBLE ACTIVATION" in r["text"]
    assert r["page"] == 10
    assert "error" not in r and "note" not in r


def test_normalization():
    _fresh()
    for raw in ("z1.11", " Z1.11 ", "Z1.11.", "z 1.11"):
        r = rules_lookup.get_section(raw)
        assert r.get("section") == "Z1.11", f"{raw!r} -> {r}"


def test_parent_fallback_missing_child():
    _fresh()
    r = rules_lookup.get_section("Z1.115")  # doesn't exist
    assert r["section"] == "Z1.11"
    assert "not found; returning parent Z1.11" in r["note"]


def test_parent_fallback_null_text():
    _fresh()
    # Z3 exists but extracted empty (text null) -> no ancestor with text ->
    # error with hints (Z3 has no dotted parent).
    r = rules_lookup.get_section("Z3")
    assert "error" in r


def test_did_you_mean():
    _fresh()
    r = rules_lookup.get_section("Z4.1")
    assert "error" in r
    assert any(h.startswith("Z") for h in r.get("did_you_mean", [])), r


def test_subsections():
    _fresh()
    r = rules_lookup.get_section("Z1.1", include_subsections=True)
    subs = [s["section"] for s in r["subsections"]]
    assert subs == ["Z1.11", "Z1.12"], subs
    # Z1 -> direct children only (one dotted level): Z1.1, Z1.2 — not Z1.11
    r = rules_lookup.get_section("Z1", include_subsections=True)
    subs = [s["section"] for s in r["subsections"]]
    assert subs == ["Z1.1", "Z1.2"], subs


def test_qa_attachment_and_default_on():
    _fresh()
    r = rules_lookup.get_section("Z1.1")
    assert len(r["qa"]) == 2
    assert any("FIX2" in e["text"] for e in r["qa"])
    r2 = rules_lookup.get_section("Z1.1", include_qa=False)
    assert "qa" not in r2


def test_qa_multi_key_entry_appears_under_both():
    _fresh()
    r = rules_lookup.get_section("Z2.1")
    assert any("FIX2" in e["text"] for e in r["qa"])


def test_errata_kind_preserved():
    _fresh()
    r = rules_lookup.get_section("Z1.2")
    assert r["qa"][0]["kind"] == "errata"


def test_missing_sections_store_is_clean_error():
    rules_lookup.reset()
    rules_lookup.load_sections("/nonexistent/sections.json", "/nonexistent/qa.json")
    r = rules_lookup.get_section("Z1.1")
    assert r == {"error": rules_lookup.NOT_BUILT_ERROR}


def test_missing_qa_store_degrades_to_empty():
    rules_lookup.reset()
    rules_lookup.load_sections(SECTIONS, "/nonexistent/qa.json")
    r = rules_lookup.get_section("Z1.1")
    assert r["qa"] == [] and "error" not in r


def test_valid_section_ids():
    _fresh()
    ids = rules_lookup.valid_section_ids()
    assert "Z1.11" in ids and "Z4" not in ids


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
