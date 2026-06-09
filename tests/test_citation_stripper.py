#!/usr/bin/env python
"""
Tests for _CitationStripper: removes OpenAI file_search citation markers
(PUA-delimited) from streamed answer text. Runnable directly or via pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.asl_service import _CitationStripper

# Real OpenAI citation markers use Private Use Area delimiters. Built via chr()
# so the source stays plain ASCII: U+E200 start, U+E202 separator, U+E201 end.
_S, _SEP, _E = chr(0xE200), chr(0xE202), chr(0xE201)


def _cite(token):
    return f"{_S}filecite{_SEP}{token}{_E}"


def test_removes_marker_and_leading_space():
    s = _CitationStripper()
    out = s.feed(f"Stone buildings give a +3 TEM {_cite('turn0file3')} and confirm it.")
    out += s.flush()
    assert "filecite" not in out and "turn0file3" not in out
    assert not any(0xE000 <= ord(c) <= 0xF8FF for c in out)
    assert out == "Stone buildings give a +3 TEM and confirm it.", repr(out)


def test_punctuation_terminator():
    s = _CitationStripper()
    out = s.feed(f"Sniper requires a game result {_cite('turn0file11')}.") + s.flush()
    assert out == "Sniper requires a game result.", repr(out)


def test_split_across_chunks():
    s = _CitationStripper()
    out = s.feed("the +3 TEM " + _S + "file")
    out += s.feed("cite" + _SEP + "turn0file3" + _E + " and the rest")
    out += s.flush()
    assert out == "the +3 TEM and the rest", repr(out)


def test_plain_text_untouched():
    s = _CitationStripper()
    text = "On the 8 FP column at +3, NMC on 5-6. See A6.4 and B23.2."
    assert s.feed(text) + s.flush() == text


def test_marker_at_end_drops_trailing_space():
    s = _CitationStripper()
    out = s.feed(f"final note {_cite('turn0file9')}") + s.flush()
    assert out == "final note", repr(out)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
