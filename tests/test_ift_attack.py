#!/usr/bin/env python
"""
Tests for the IFT Attack Builder (`ift.compute_attack` + the `ift_attack`
agentic tool). Golden cases come straight from the rulebook examples cited in
docs/ift_attack_tool_plan.md (A7.2–A7.9, A4.6).

Pure engine + tool wrapper — no network, no app server. Runnable directly
(`python tests/test_ift_attack.py`) or under pytest.
"""
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import ift
from app.asl.ift import compute_attack, _personnel_outcome, _mc_probs
from app.asl.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS, execute_tool, ift_attack


def _schema(name):
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)


# --------------------------------------------------------------------------- #
# Layer 1 — firepower resolution (A7.2, A7.21–.24, A7.31 EX, A7.36)
# --------------------------------------------------------------------------- #

def test_a731_ex_afph_assault_fire():
    """A7.31 EX: a 6-6-6 in AFPh with assault fire: 6 ÷2 = 3, +1 = 4 FP."""
    r = compute_attack(units=[{"fp": 6, "assault_fire": True}], afph=True)
    assert r["total_fp"] == 4 and r["column"] == 4
    u = r["fp_breakdown"][0]
    assert u["final"] == 4
    assert u["steps"] == ["÷2 AFPh = 3", "+1 assault fire → 4"]


def test_a731_ex_two_548_pbf_afph_concealed():
    """
    A7.31 EX: two 5-4-8s at PBF in AFPh vs a concealed target, assault fire:
    each 5 ×2 = 10, ÷2 = 5, ÷2 = 2.5, +1 FRU = 4 → total 8 → 8 column.
    """
    unit = {"fp": 5, "pbf": "pbf", "assault_fire": True}
    r = compute_attack(units=[dict(unit), dict(unit)], afph=True, area_fire_halvings=1)
    assert r["total_fp"] == 8 and r["column"] == 8
    for u in r["fp_breakdown"]:
        assert u["final"] == 4
        assert u["steps"] == [
            "×2 PBF = 10", "÷2 AFPh = 5", "÷2 area fire = 2.5",
            "+1 assault fire → 4 (FRU)",
        ]


def test_a734_ex_long_range_fraction_retained():
    """A7.24/A7.34 EX: 3 FP halved for long range → 1.5 retained → 1 column."""
    r = compute_attack(units=[{"fp": 3, "long_range": True}])
    assert r["total_fp"] == 1.5 and r["column"] == 1


def test_fractions_summed_across_units_before_column():
    """A7.2: fractions are kept per unit and only the TOTAL picks the column."""
    # Two 3 FP units at long range: 1.5 + 1.5 = 3 → 2 column (not 1+1).
    r = compute_attack(units=[{"fp": 3, "long_range": True}] * 2)
    assert r["total_fp"] == 3 and r["column"] == 2


def test_below_one_fp_is_no_attack():
    """A7.3: a total below the 1 FP column is no valid attack — explicit error."""
    r = compute_attack(units=[{"fp": 1, "long_range": True}])
    assert r["column"] is None
    assert "error" in r and "distribution" not in r
    assert r["total_fp"] == 0.5  # the math that got there is still echoed


def test_assault_fire_na_at_long_range():
    """A7.36: assault fire's +1 is NA at long range — dropped with a warning."""
    r = compute_attack(units=[{"fp": 4, "long_range": True, "assault_fire": True}])
    assert r["total_fp"] == 2  # no +1
    assert any("assault fire" in w.lower() for w in r["warnings"])


def test_opportunity_fire_negates_afph_and_assault():
    """A7.25: opportunity fire skips the AFPh halving; assault fire NA."""
    r = compute_attack(units=[{"fp": 4, "assault_fire": True}],
                       afph=True, opportunity_fire=True)
    assert r["total_fp"] == 4  # no ÷2, no +1
    assert any("opportunity fire" in w for w in r["warnings"])


def test_tpbf_and_pinned():
    """A7.21 TPBF ×3; A7.8 pinned firer ×½."""
    r = compute_attack(units=[{"fp": 4, "pbf": "tpbf", "pinned": True}])
    assert r["total_fp"] == 6 and r["column"] == 6


# --------------------------------------------------------------------------- #
# Layer 2 — DRM ledger (A7.6, A4.6)
# --------------------------------------------------------------------------- #

def test_drm_ledger_sums_and_itemizes():
    """A7.2 EX-style ledger: +1 hindrance, +1 TEM, -1 FFNAM = +1."""
    r = compute_attack(units=[{"fp": 4}], tem=1, hindrance=1, ffnam=True)
    assert r["drm"] == 1
    assert [(d["label"], d["drm"]) for d in r["drm_breakdown"]] == [
        ("TEM", 1), ("hindrance", 1), ("FFNAM", -1),
    ]


def test_ffmo_negated_by_hindrance_with_warning():
    """A4.6: FFMO is negated by any hindrance — dropped loudly, not silently."""
    r = compute_attack(units=[{"fp": 4}], hindrance=1, ffmo=True)
    assert r["drm"] == 1  # hindrance only; the -1 did NOT apply
    assert all(d["label"] != "FFMO" for d in r["drm_breakdown"])
    assert any("FFMO" in w for w in r["warnings"])


def test_ffmo_negated_by_tem_with_warning():
    """A4.6: positive in-hex TEM also negates FFMO."""
    r = compute_attack(units=[{"fp": 4}], tem=2, ffmo=True)
    assert r["drm"] == 2
    assert any("FFMO" in w for w in r["warnings"])


def test_ffmo_applies_in_true_open_ground():
    r = compute_attack(units=[{"fp": 4}], ffnam=True, ffmo=True)
    assert r["drm"] == -2 and not r["warnings"]


def test_other_drm_and_encircled_firer():
    r = compute_attack(units=[{"fp": 4}], encircled_firer=True,
                       other_drm=[{"label": "air bursts", "drm": -1}])
    assert r["drm"] == 0
    labels = [d["label"] for d in r["drm_breakdown"]]
    assert "encircled firer" in labels and "air bursts" in labels


# --------------------------------------------------------------------------- #
# Layer 3 — cowering derivation (A7.9)
# --------------------------------------------------------------------------- #

def test_cowering_derivation():
    assert compute_attack(units=[{"fp": 4}])["cowering"] == "regular"
    assert compute_attack(units=[{"fp": 4}], leadership=-1)["cowering"] == "none"
    assert compute_attack(units=[{"fp": 4}], firer_cowering_exempt=True)["cowering"] == "none"
    assert compute_attack(units=[{"fp": 4}], inexperienced=True)["cowering"] == "double"
    # Leader direction trumps inexperience (A7.9).
    assert compute_attack(units=[{"fp": 4}], leadership=-1,
                          inexperienced=True)["cowering"] == "none"


# --------------------------------------------------------------------------- #
# Layer 4 — target effects (A7.301–.308, A7.8)
# --------------------------------------------------------------------------- #

def test_personnel_branch_nmc_vs_morale_7():
    """NMC vs morale 7: break = P(2d6>7) = 15/36, pin = P(2d6==7) = 6/36."""
    out = _personnel_outcome("NMC", morale=7, mc_drm=0)
    assert out["broken"] == Fraction(15, 36)
    assert out["pinned"] == Fraction(6, 36)
    assert out["no_effect"] == Fraction(15, 36)


def test_personnel_branch_2mc_applies_penalty():
    """2MC vs morale 7 ≡ NMC vs morale 5: break = P(2d6>5) = 26/36."""
    out = _personnel_outcome("2MC", morale=7, mc_drm=0)
    assert out["broken"] == Fraction(26, 36)
    assert out["pinned"] == Fraction(4, 36)  # P(2d6 == 5)


def test_personnel_branch_ptc_pins_on_failure_only():
    """A7.305: PTC pins on a FAILED check — an exact pass is a pass."""
    out = _personnel_outcome("PTC", morale=7, mc_drm=0)
    assert out["pinned"] == Fraction(15, 36)   # P(2d6 > 7)
    assert out["no_effect"] == Fraction(21, 36)


def test_personnel_branch_kia_and_k():
    out = _personnel_outcome("2KIA", morale=7, mc_drm=0)
    assert out["eliminated_or_reduced"] == 1
    out = _personnel_outcome("K/2", morale=7, mc_drm=0)
    assert out["eliminated_or_reduced"] == 1  # reduction is certain
    # Survivor's 2MC rides along: P(2d6+2 > 7) = 26/36, exact pass 4/36.
    assert out["survivor_broken"] == Fraction(26, 36)
    assert out["survivor_pinned"] == Fraction(4, 36)


def test_mc_probs_drm_shifts():
    """A leader -1 in the target hex makes the MC easier."""
    base = _mc_probs(0, 7)
    helped = _mc_probs(-1, 7)
    assert helped["fail"] < base["fail"]


def test_personnel_headline_categories_sum_to_one():
    r = compute_attack(units=[{"fp": 8}], target={"kind": "personnel", "morale": 7})
    vt = r["vs_target"]
    total = (vt["p_eliminated_or_reduced"] + vt["p_broken"]
             + vt["p_pinned"] + vt["p_no_effect"])
    assert abs(total - 1.0) < 0.001, f"categories should sum to 1, got {total}"


def test_personnel_encircled_lowers_morale():
    """A7.7: an encircled target's morale drops by 1 vs the attack."""
    base = compute_attack(units=[{"fp": 8}],
                          target={"kind": "personnel", "morale": 7})
    enc = compute_attack(units=[{"fp": 8}],
                         target={"kind": "personnel", "morale": 7, "encircled": True})
    assert enc["vs_target"]["effective_morale"] == 6
    assert enc["vs_target"]["p_broken"] > base["vs_target"]["p_broken"]


def test_vehicle_target_kill_numbers():
    """
    A7.308 ★ line, 8 FP column (kill# 7), DRM 0, no cowering:
      burning ≤ floor(7/2)=3 → 3/36; eliminated 4–6 → 12/36;
      immobilized == 7 → 6/36; no effect → 15/36.
    """
    r = compute_attack(units=[{"fp": 8}], firer_cowering_exempt=True,
                       target={"kind": "vehicle"})
    vt = r["vs_target"]
    assert vt["kill_numbers"] == {"8": 7}
    assert vt["p_burning_wreck"] == round(3 / 36, 4)
    assert vt["p_eliminated"] == round(12 / 36, 4)
    assert vt["p_immobilized"] == round(6 / 36, 4)
    assert vt["p_no_effect"] == round(15 / 36, 4)


def test_vehicle_convolved_against_post_cowering_columns():
    """With cowering, doubles shift to the 6 column (kill# 6) — the vehicle
    math must read the kill# of the column the DR was actually resolved on."""
    r = compute_attack(units=[{"fp": 8}], target={"kind": "vehicle"})
    assert set(r["vs_target"]["kill_numbers"]) == {"8", "6"}


def test_san_passthrough():
    r = compute_attack(units=[{"fp": 8}], san=4)
    assert r["sniper"]["san"] == 4 and r["sniper"]["p_trigger"] == round(3 / 36, 4)
    assert compute_attack(units=[{"fp": 8}])["sniper"] is None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_invalid_inputs_raise():
    for bad in (
        lambda: compute_attack(units=[]),
        lambda: compute_attack(units=[{"fp": 0}]),
        lambda: compute_attack(units=[{"fp": 4, "pbf": "point-blank"}]),
        lambda: compute_attack(units=[{"fp": 4}], area_fire_halvings=-1),
        lambda: compute_attack(units=[{"fp": 4}], target={"kind": "tank"}),
        lambda: compute_attack(units=[{"fp": 4}], target={"kind": "personnel"}),  # no morale
    ):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError(f"{bad} should have raised ValueError")


# --------------------------------------------------------------------------- #
# Agentic tool wrapper + schema
# --------------------------------------------------------------------------- #

def test_ift_attack_tool_strips_cells():
    r = ift_attack(units=[{"fp": 8}], san=3)
    assert "cells" not in r, "UI-only heatmap should be stripped from tool output"
    assert r["column"] == 8 and r["total_fp"] == 8


def test_ift_attack_registered_and_dispatchable():
    assert "ift_attack" in TOOL_FUNCTIONS
    r = execute_tool("ift_attack", {
        "units": [{"fp": 5, "pbf": "pbf", "assault_fire": True}],
        "afph": True, "area_fire_halvings": 1,
        "target": {"kind": "personnel", "morale": 7},
    })
    assert r["column"] == 4  # one 5-4-8: 10 → 5 → 2.5 → +1 FRU = 4
    assert r["vs_target"]["kind"] == "personnel"


def test_ift_attack_schema_enums_match_engine():
    props = _schema("ift_attack")["parameters"]["properties"]
    assert props["units"]["items"]["properties"]["pbf"]["enum"] == list(ift.PBF_MULTIPLIER)
    assert props["target"]["properties"]["kind"]["enum"] == ["personnel", "vehicle"]
    assert _schema("ift_attack")["parameters"]["required"] == ["units"]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

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
