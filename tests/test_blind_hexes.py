#!/usr/bin/env python
"""
Tests for the deterministic Blind Hex calculator (app/asl/blind_hexes.py)
and its agentic tool wrapper.

Every worked example in the eASLRB v3.14 text is reproduced: the A6.42
example (level 1½ obstacle at 12 hexes), the A6.43 example (2J4 over the
I3 woods), B10.23 examples (A)-(G) on board 15, B10.23's nominal range
table for a one-level Crest Line, and the official B11.21 Q&A (level 2
firer, level 1 cliff at 5-9 hexes -> two Blind Hexes).

Board elevations for the B10.23 examples are inferred from the example text
itself (e.g. "Y6 (level 4)", "DD3 (level 1)", "each Crest Line creates only
a one elevation level drop").

Runnable directly (`python tests/test_blind_hexes.py`) or under pytest.
No network, no DB.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl.blind_hexes import compute_blind_hexes
from app.asl.tools import TOOL_SCHEMAS, CALC_TOOL_NAMES, execute_tool


def _blind(F, O, R, T, H=0, cliff=False):
    r = compute_blind_hexes(firer_level=F, obstacle_hex_level=O, range_to_obstacle=R,
                            hexes_behind=T, obstacle_height=H, cliff=cliff)
    return [h["blind"] for h in r["hexes"]]


def _count(*a, **k):
    return sum(_blind(*a, **k))


# --------------------------------------------------------------------------- #
# Rulebook examples
# --------------------------------------------------------------------------- #

def test_a642_example_level_one_and_half_obstacle_at_twelve_hexes():
    """A6.42 EX: 1½ obstacle (full level 1) at 12 hexes -> 3 blind for a level-2
    firer, 2 for level 3, 1 for level 4 or higher (min one)."""
    assert [_count(F, 0, 12, [0] * 5, H=1) for F in (2, 3, 4, 5, 6)] == [3, 2, 1, 1, 1]


def test_a643_example_2j4_over_i3_woods():
    """A6.43 EX: 2J4 (level 3) over level-1 woods on the level-1 hill in I3
    (adjacent). H2 (level 1) is blind; H1 (level 0) is blind because the I3-H1
    difference is added. Had H1 been level 1, LOS would exist; had J4 been
    level 4, LOS to H1 would exist (A6.42)."""
    assert _blind(3, 1, 1, [1, 0], H=1) == [True, True]
    assert _blind(3, 1, 1, [1, 1], H=1) == [True, False]
    assert _blind(4, 1, 1, [1, 0], H=1) == [True, False]


def test_b1023_example_c_advantage_negates_crest_blind_hex():
    """(C) Y6 (level 4) sees EE3 past the level-1 Crest Line in DD3 at 5 hexes:
    the three-level advantage negates the Blind Hex (to zero, non-cliff)."""
    assert _blind(4, 1, 5, [0]) == [False]


def test_b1023_example_d_one_level_advantage_cannot_negate():
    """(D) Y6 (level 4) cannot see EE5: the level-3 Crest Line in DD5 at 5
    hexes creates one Blind Hex the one-level advantage cannot negate."""
    assert _blind(4, 3, 5, [2]) == [True]


def test_b1023_example_e_two_blind_at_eleven_reduced_by_advantage():
    """(E) X6 (level 3) cannot see K7: the level-2 Crest Line in M7 creates two
    Blind Hexes at 11 hexes. From Y6 (level 4) at 12 hexes the two-level
    advantage reduces them to one."""
    assert _count(3, 2, 11, [1, 1, 1]) == 2
    assert _count(4, 2, 12, [1, 1, 1]) == 1


def test_b1023_example_f_woods_on_hill_over_lower_terrain():
    """(F) CC5 (level 3) cannot see CC1: level-1 woods on the level-1 hill in
    CC3 (2 hexes) cause two Blind Hexes over the level-0 terrain behind."""
    assert _blind(3, 1, 2, [0, 0], H=1) == [True, True]


def test_b1023_example_g_building_one_blind_unless_terrain_behind_is_lower():
    """(G) Q4 (level 3) can see M2 because the O3 building (level 1 hill) is a
    one-level obstacle creating one Blind Hex (N2). Were M2 level 0, two."""
    assert _blind(3, 1, 2, [1, 1], H=1) == [True, False]
    assert _blind(3, 1, 2, [1, 0], H=1) == [True, True]


def test_b1023_example_a_cliff_two_level_drop_within_four_hexes():
    """(A) 15Y6 (level 4) cannot see U7 four hexes away: the V6-U7 cliff drops
    two levels, creating a Blind Hex (and B11.21 keeps it)."""
    assert _blind(4, 2, 4, [0], cliff=True) == [True]


def test_b1121_qa_level_one_cliff_at_five_to_nine_hexes_is_two_blind():
    """Q&A B11.21: unit at level 2, level-1 cliff 5-9 hexes away, LOS to a
    level-0 Location -> TWO Blind Hexes (cliff adds the full drop, A6.43)."""
    for R in range(5, 10):
        assert _count(2, 1, R, [0] * 4, cliff=True) == 2, R
    # ...whereas the same geometry over a non-cliff Crest Line is one.
    assert _count(2, 1, 7, [0] * 4) == 1


def test_b1023_nominal_range_table_one_level_crest():
    """B10.23: a one-level Crest Line creates 0 Blind Hexes at range 1-4, one at
    5-9, two at 10-14, three at 15-19."""
    expected = {1: 0, 4: 0, 5: 1, 9: 1, 10: 2, 14: 2, 15: 3, 19: 3}
    for R, n in expected.items():
        assert _count(2, 1, R, [0] * 6) == n, (R, n)


def test_b1023_exc_two_level_drop_within_five_hexes():
    """B10.23 EXC: a >= 2-level non-cliff drop creates at least one Blind Hex
    even within five hexes - unless adjacent."""
    assert _count(3, 2, 3, [0, 0]) == 1
    assert _count(3, 2, 1, [0, 0]) == 0
    # Large elevation advantage does not negate the EXC hex (documented reading).
    assert _count(6, 2, 3, [0, 0]) == 1


def test_b1121_cliff_minimum_one_regardless_of_advantage():
    assert _count(9, 1, 2, [0, 0], cliff=True) == 1
    assert _count(9, 1, 1, [0, 0], cliff=True) == 0  # adjacent viewer


# --------------------------------------------------------------------------- #
# Edge behaviour
# --------------------------------------------------------------------------- #

def test_hex_at_or_above_obstacle_top_is_never_blind():
    r = compute_blind_hexes(firer_level=3, obstacle_hex_level=0, range_to_obstacle=10,
                            hexes_behind=[0, 1, 0], obstacle_height=1)
    # 1 + 2 (range) - 1 (advantage 2) = 2 blind: hex 1 blind, hex 2 is at the
    # obstacle top so never blind, hex 3 is beyond the count.
    assert [h["blind"] for h in r["hexes"]] == [True, False, False]
    assert r["hexes"][1]["reason"].startswith("at level 1 >= obstacle top 1")


def test_higher_hex_behind_subtracts():
    """A6.43: a hex behind that is higher than the obstacle hex subtracts."""
    # 2-story building at level 0, range 10, firer level 3: 2 + 2 - 0 = 4 blind
    assert _count(3, 0, 10, [0, 0, 0, 0, 0], H=2) == 4
    # ...a level-1 hex in position 3: 4 - 1 = 3 >= 3 -> still blind; in
    # position 4: 3 < 4 -> visible; position 5 (level 0) is beyond the count.
    assert _blind(3, 0, 10, [0, 0, 1, 1, 0], H=2) == [True, True, True, False, False]


def test_firer_not_above_obstacle_is_blocked_not_blind():
    r = compute_blind_hexes(firer_level=1, obstacle_hex_level=0, range_to_obstacle=3,
                            hexes_behind=[0, 1, 2], obstacle_height=1)
    assert r["sees_over_obstacle"] is False
    assert [h["blind"] for h in r["hexes"]] == [True, False, False]
    assert r["base_blind"] is None


def test_output_shape_and_ledger():
    r = compute_blind_hexes(firer_level=4, obstacle_hex_level=1, range_to_obstacle=12,
                            hexes_behind=[0, 0, 0], obstacle_height=1)
    assert r["obstacle_kind"] == "level 1 terrain obstacle"
    assert r["inputs"]["obstacle_top"] == 2
    assert r["base_blind"] == 1 and r["range_bonus"] == 2
    assert r["elevation_advantage"] == 2 and r["advantage_reduction"] == 1
    assert r["count_before_terrain_behind"] == 2
    assert r["blind_count"] == 3 and r["blind_hexes"] == [1, 2, 3]
    assert r["visible_hexes"] == []
    assert any("A6.41" in s for s in r["steps"])
    assert any("A6.43" in s for s in r["steps"])
    assert "B11.21" not in r["rules"]
    json.dumps(r)  # tool outputs must be JSON-serializable


def test_validation():
    for bad in (
        dict(firer_level=2, obstacle_hex_level=1, range_to_obstacle=0, hexes_behind=[0]),
        dict(firer_level=2, obstacle_hex_level=1, range_to_obstacle=3, hexes_behind=[]),
        dict(firer_level=2, obstacle_hex_level=1, range_to_obstacle=3, hexes_behind=[0],
             obstacle_height=-1),
        dict(firer_level=2.5, obstacle_hex_level=1, range_to_obstacle=3, hexes_behind=[0]),
        dict(firer_level="2", obstacle_hex_level=1, range_to_obstacle=3, hexes_behind=[0]),
    ):
        try:
            compute_blind_hexes(**bad)
        except ValueError:
            continue
        raise AssertionError(f"should have raised: {bad}")
    # Whole-number floats are accepted (JSON round-trips may produce 2.0).
    assert _count(2.0, 1.0, 7.0, [0.0] * 3) == 1


# --------------------------------------------------------------------------- #
# Agentic tool wrapper
# --------------------------------------------------------------------------- #

def test_tool_registered_as_calculator_with_schema():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "blind_hexes")
    assert "blind_hexes" in CALC_TOOL_NAMES
    props = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) == {
        "firer_level", "obstacle_hex_level", "range_to_obstacle", "hexes_behind"}
    assert props["hexes_behind"]["items"]["type"] == "integer"
    assert props["cliff"]["type"] == "boolean"


def test_tool_dispatch_matches_engine():
    args = {"firer_level": 2, "obstacle_hex_level": 1, "range_to_obstacle": 7,
            "hexes_behind": [0, 0, 0], "cliff": True}
    r = execute_tool("blind_hexes", args)
    assert r["blind_hexes"] == [1, 2] and r["visible_hexes"] == [3]
    assert r == compute_blind_hexes(**args)
    json.dumps(r)


def test_tool_dispatch_drops_stray_argument():
    r = execute_tool("blind_hexes", {"firer_level": 3, "obstacle_hex_level": 1,
                                     "range_to_obstacle": 2, "hexes_behind": [0, 0],
                                     "obstacle_height": 1, "include_subsections": True})
    assert r["blind_count"] == 2


if __name__ == "__main__":
    import inspect
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
