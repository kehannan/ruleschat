#!/usr/bin/env python
"""
Tests for the deterministic attack resolver (app/asl/attack_resolver.py)
and its agentic tool wrapper.

Anchored on the real failure case: with the Hazmo fixture, an LLM deriving
its own inputs answered "8 FP +1 DRM" for "units in 57-H9 prep-fire at
57-H8". Correct is 16 FP (6+2 doubled by A7.21 PBF at range 1) and +2 DRM
(B27.3 foxhole TEM). The resolver must produce that from the parsed save.

Runnable directly (`python tests/test_attack_resolver.py`) or under pytest.
No network, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import attack_resolver
from app.asl.attack_resolver import (
    classify_unit,
    hex_range_same_board,
    resolve_attack,
)
from app.asl.tools import TOOL_SCHEMAS, execute_tool
from app.asl.tools import resolve_attack as resolve_attack_tool
from app.services.vsav_service import parse_vsav

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"

_STATE = None


def _state():
    global _STATE
    if _STATE is None:
        _STATE = parse_vsav(FIXTURE)
    return _STATE


def _mk_state(hexes):
    """Minimal synthetic parse_vsav-shaped state (single board 57)."""
    return {
        "hexes": hexes,
        "boards": [{
            "name": "57", "base": "57", "slot": [0, 0], "version": None,
            "reversed": False, "crop": {"x": 0, "y": 0, "w": -1, "h": -1},
            "ssr_transforms": [],
        }],
    }


def _sq(name, side, markers=None, **kw):
    u = {"name": name, "side": side}
    if markers:
        u["markers"] = list(markers)
    u.update(kw)
    return u


# --------------------------------------------------------------------------- #
# (1) The Hazmo H9 -> H8 case: 16 FP, +2 DRM, PBF + foxhole itemized
# --------------------------------------------------------------------------- #

def test_hazmo_h9_prep_fires_at_h8():
    r = resolve_attack(_state(), "57-H9", "57-H8", phase="prep")

    assert r["firing_side"] == "Finnish", r["firing_side"]
    assert r["total_fp"] == 16, f"expected 16 FP (6+2 doubled by PBF), got {r['total_fp']}"
    assert r["column"] == 16, r["column"]
    assert r["drm"] == 2, f"expected +2 DRM (B27 foxhole), got {r['drm']}"

    # PBF itemized: range 1, mode pbf, and x2 steps in the per-unit audit.
    assert r["range"]["hexes"] == 1 and r["range"]["pbf"] == "pbf", r["range"]
    assert "A7.21" in (r["range"]["note"] or ""), r["range"]
    squad = next(f for f in r["firers"] if f["name"].startswith("6-4-8"))
    lmg = next(f for f in r["firers"] if "LMG" in f["name"])
    assert squad["fp"] == 6 and lmg["fp"] == 2, (squad, lmg)
    assert any("PBF" in s for s in squad["fp_resolution"]["steps"]), squad
    assert squad["fp_resolution"]["final"] == 12, squad
    assert lmg["fp_resolution"]["final"] == 4, lmg

    # Foxhole TEM itemized with the B27 cite; replaces terrain TEM.
    fox = [d for d in r["drm_breakdown"] if "Foxhole" in d["label"]]
    assert fox and fox[0]["drm"] == 2 and "B27.3" in fox[0]["label"], r["drm_breakdown"]
    assert r["target"]["entrenched"] is True

    # Containment comes from STACK ORDER, not assumption: in the fixture the
    # H8 Foxhole is the TOP counter of its stack, so every H8 unit is under
    # it (entrenched_by per unit), and the old "assumed to be IN" line is gone.
    assert all(t["entrenched_by"] == "Foxhole" for t in r["target"]["units"]), \
        r["target"]["units"]
    assert any("stack order" in a for a in r["assumptions"]), r["assumptions"]
    assert not any("assumed to be IN" in a for a in r["assumptions"]), r["assumptions"]

    # LOS explicitly in the assumptions.
    assert any("LOS" in a for a in r["assumptions"]), r["assumptions"]

    # The abandoned Russian 50* MTR must not fire (enemy + ordnance).
    mtr = [e for e in r["excluded"] if "MTR" in e["name"]]
    assert mtr and "enemy" in mtr[0]["reason"].lower(), r["excluded"]

    # Finns do not cower (A7.9).
    assert r["cowering"] == "none", r["cowering"]

    # Break/pin odds vs the Russian 4-4-7's printed morale 7.
    assert r["target"]["morale_used"] == 7, r["target"]
    vs = r["ift"]["vs_target"]
    assert vs and vs["kind"] == "personnel" and vs["morale"] == 7, vs
    assert vs["p_broken"] > 0.4, vs  # 16-col +2 vs morale 7 breaks a lot


# --------------------------------------------------------------------------- #
# (2) FP parsing from counter names incl. the SW table
# --------------------------------------------------------------------------- #

def test_fp_parsing_from_names():
    c = classify_unit({"name": "6-4-8 1sq", "side": "Finnish"})
    assert (c["kind"], c["fp"], c["normal_range"], c["morale"]) == ("personnel", 6, 4, 8), c
    c = classify_unit({"name": "4-4-7 1sq", "side": "Russian"})
    assert (c["fp"], c["normal_range"], c["morale"]) == (4, 4, 7), c
    c = classify_unit({"name": "2-2-8 Icr", "side": "Russian"})
    assert c["kind"] == "personnel" and c["fp"] == 2, c

    # SW table — values read from VASL 6.7.3 counter art.
    c = classify_unit({"name": "LMG (r)", "side": "Finnish"})
    assert (c["kind"], c["fp"], c["normal_range"]) == ("sw", 2, 6), c
    c = classify_unit({"name": "LMG", "side": "Finnish"})
    assert (c["fp"], c["normal_range"]) == (3, 8), c
    c = classify_unit({"name": "MMG", "side": "Russian"})
    assert (c["fp"], c["normal_range"]) == (4, 10), c
    c = classify_unit({"name": "HMG", "side": "German"})
    assert (c["fp"], c["normal_range"]) == (7, 16), c
    c = classify_unit({"name": "ATR", "side": "Russian"})
    assert (c["fp"], c["normal_range"], c["no_long_range"]) == (1, 12, True), c
    # Unknown nationality falls back to generic values with a note.
    c = classify_unit({"name": "MMG", "side": "AxisMinor"})
    assert (c["fp"], c["normal_range"]) == (4, 10) and c["detail"], c

    # Leaders: concrete vs generic.
    c = classify_unit({"name": "9-1", "side": "German"})
    assert c["kind"] == "leader" and c["leadership"] == -1 and c["morale"] == 9, c
    c = classify_unit({"name": "ruLDR", "side": "Russian"})
    assert c["kind"] == "leader" and c["leadership"] is None, c

    # Ordnance: mortars and Guns are not IFT firers.
    assert classify_unit({"name": "50* MTR", "side": "Russian"})["kind"] == "ordnance"
    assert classify_unit({"name": "37L AT PTP obr. 30", "side": "Russian"})["kind"] == "ordnance"


# --------------------------------------------------------------------------- #
# (3) Range / PBF / TPBF / long-range boundaries
# --------------------------------------------------------------------------- #

def test_hex_range_same_board():
    assert hex_range_same_board("H9", "H8") == 1
    assert hex_range_same_board("H9", "H9") == 0
    assert hex_range_same_board("H9", "G9") == 1   # adjacent across columns
    assert hex_range_same_board("H9", "K9") == 3
    assert hex_range_same_board("A1", "A10") == 9
    assert hex_range_same_board("B2", "D2") == 2


def _two_hex_state(target_hex, target_markers=None, extra_firing=None,
                   firing_units=None):
    funits = firing_units or [_sq("4-6-7 1sq", "German")]
    if extra_firing:
        funits += extra_firing
    tunits = [_sq("4-4-7 1sq", "Russian", markers=target_markers)]
    hexes = {
        "57-B2": {"units": funits, "markers": []},
        target_hex: {"units": tunits, "markers": list(target_markers or [])},
    }
    return _mk_state(hexes)


def test_pbf_applies_only_at_range_1():
    # Range 1: doubled.
    s = _two_hex_state("57-B3")
    r = resolve_attack(s, "57-B2", "57-B3")
    assert r["range"]["hexes"] == 1 and r["range"]["pbf"] == "pbf"
    assert r["total_fp"] == 8, r["total_fp"]
    # Range 2: no PBF.
    s = _two_hex_state("57-B4")
    r = resolve_attack(s, "57-B2", "57-B4")
    assert r["range"]["pbf"] == "none" and r["total_fp"] == 4, r


def test_tpbf_same_hex():
    hexes = {"57-B2": {
        "units": [_sq("4-6-7 1sq", "German"), _sq("4-6-7 2sq", "German"),
                  _sq("4-4-7 1sq", "Russian")],
        "markers": [],
    }}
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B2")
    assert r["range"]["hexes"] == 0 and r["range"]["pbf"] == "tpbf", r["range"]
    assert r["total_fp"] == 24, r["total_fp"]  # (4+4) x3
    assert any("same" in w.lower() and "location" in w.lower()
               for w in r["warnings"]), r["warnings"]


def test_long_range_halving_and_max_range():
    # 4-6-7 has Normal Range 6: at range 7..12 it fires half FP (A7.22).
    s = _two_hex_state("57-B9")  # B2 -> B9 = 7 hexes
    r = resolve_attack(s, "57-B2", "57-B9")
    assert r["range"]["hexes"] == 7, r["range"]
    assert r["total_fp"] == 2, r["total_fp"]
    assert any("A7.22" in n for f in r["firers"] for n in f["notes"]), r["firers"]
    # Beyond double range there is no attack: squad range 4 at distance 9.
    s = _mk_state({
        "57-B2": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
        "57-K4": {"units": [_sq("4-6-7 1sq", "German")], "markers": []},
    })
    try:
        resolve_attack(s, "57-B2", "57-K4")
    except ValueError as e:
        assert "excluded" in str(e).lower() or "A7.22" in str(e), e
    else:
        raise AssertionError("attack beyond double Normal Range should fail")


def test_cross_board_range_warns():
    s = _state()
    keys = list(s["hexes"])
    k69 = next(k for k in keys if k.startswith("69-"))
    k57 = next(k for k in keys if k.startswith("57-"))
    try:
        r = resolve_attack(s, k69, k57)
    except ValueError:
        return  # no eligible firers / out of range is fine for this check
    assert any("different boards" in w for w in r["warnings"]), r["warnings"]


# --------------------------------------------------------------------------- #
# (4) Entrenchment TEM replaces terrain TEM (B27.3 / B27.13)
# --------------------------------------------------------------------------- #

def test_entrenchment_tem_replaces_terrain_tem():
    woods = {"terrain": "Woods", "parts": ["Woods"], "road": False,
             "elevation": 0, "ssr_changed": {}}
    # Woods + foxhole: +2 entrenchment, NOT +3 (not cumulative).
    # `entrenched_by` is what parse_vsav now emits (stack-order containment).
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German")], "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian",
                                entrenched_by="Foxhole")],
                  "markers": [], "terrain": woods},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == 2, r["drm_breakdown"]
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "B27.3" in labels and "REPLACES" in labels, labels
    assert any("[replaced]" in d["label"] and "Woods" in d["label"]
               for d in r["drm_breakdown"]), r["drm_breakdown"]

    # Same hex without the foxhole: woods +1 (B13.3).
    hexes["57-B3"] = {"units": [_sq("4-4-7 1sq", "Russian")], "markers": [],
                      "terrain": woods}
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == 1, r["drm_breakdown"]
    assert any("B13.3" in d["label"] for d in r["drm_breakdown"]), r["drm_breakdown"]

    # Trench works like a foxhole (B27.5). Uses the legacy markers-list
    # shape on purpose: hand-built states without `entrenched_by` must keep
    # working via the fallback in _unit_entrenchment.
    hexes["57-B3"] = {"units": [_sq("4-4-7 1sq", "Russian", markers=["Trench"])],
                      "markers": ["Trench"], "terrain": woods}
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == 2 and any("Trench" in d["label"]
                                 for d in r["drm_breakdown"]), r["drm_breakdown"]


def test_mixed_tem_target_resolved_once_per_drm():
    """Stack order says one target unit is IN the foxhole (+2) and one is
    NOT (woods +1): both TEMs itemized, the IFT run once per distinct final
    DRM, loud warning, no single top-level DRM."""
    woods = {"terrain": "Woods", "parts": ["Woods"], "road": False,
             "elevation": 0, "ssr_changed": {}}
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German")], "markers": []},
        "57-B3": {"units": [
            _sq("4-4-7 1sq", "Russian", entrenched_by="Foxhole"),
            _sq("4-5-8 2sq", "Russian"),
        ], "markers": [], "terrain": woods},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["target"]["entrenched"] == "mixed", r["target"]
    assert r["drm"] is None and r["ift"] is None, (r["drm"], bool(r["ift"]))
    res = r["resolutions"]
    assert [g["tem"] for g in res] == [2, 1], res
    assert [g["drm"] for g in res] == [2, 1], res
    # per-group morale: 4-4-7 (7) in the foxhole, 4-5-8 (8) outside
    assert res[0]["morale_used"] == 7 and res[1]["morale_used"] == 8, res
    assert all(g["ift"]["column"] == 8 for g in res), res  # 4 FP x2 PBF
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "IN Foxhole" in labels and "NOT in Foxhole" in labels, labels
    assert "B27.3" in labels and "B13.3" in labels, labels
    assert any("MIXED TEM" in w for w in r["warnings"]), r["warnings"]
    # FP/column/cowering are TEM-independent and stay top-level.
    assert r["total_fp"] == 8 and r["column"] == 8, r
    ent = {t["name"]: t["entrenched_by"] for t in r["target"]["units"]}
    assert ent == {"4-4-7 1sq": "Foxhole", "4-5-8 2sq": None}, ent


def test_mixed_occupancy_equal_tem_collapses_to_one_resolution():
    """Mixed in/out of foxhole but the hex is a wooden building (+2): the
    final DRM is the same for everyone, so a single resolution covers all
    units — still itemized and noted."""
    bld = {"terrain": "Wooden Building", "parts": ["Wooden Building"],
           "road": False, "elevation": 0, "ssr_changed": {}}
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German")], "markers": []},
        "57-B3": {"units": [
            _sq("4-4-7 1sq", "Russian", entrenched_by="Foxhole"),
            _sq("4-5-8 2sq", "Russian"),
        ], "markers": [], "terrain": bld},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["target"]["entrenched"] == "mixed", r["target"]
    assert r["drm"] == 2 and "resolutions" not in r, r["drm_breakdown"]
    assert r["ift"] is not None
    assert any("MIXED occupancy" in w and "equal" in w
               for w in r["warnings"]), r["warnings"]
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "IN Foxhole" in labels and "NOT in Foxhole" in labels, labels


# --------------------------------------------------------------------------- #
# (5) Phase gating: FFMO/FFNAM never in prep/advancing
# --------------------------------------------------------------------------- #

def test_phase_gating_no_ffmo_ffnam_in_prep():
    s = _two_hex_state("57-B3")
    r = resolve_attack(s, "57-B2", "57-B3", phase="prep")
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "FFMO" not in labels and "FFNAM" not in labels, labels
    ift_labels = " | ".join(d["label"] for d in r["ift"]["drm_breakdown"])
    assert "FFMO" not in ift_labels and "FFNAM" not in ift_labels, ift_labels
    assert any("FFMO" in a and "A4.6" in a for a in r["assumptions"]), r["assumptions"]

    # Advancing fire: still no FFMO/FFNAM, and FP halved (A7.24).
    r = resolve_attack(s, "57-B2", "57-B3", phase="advancing")
    assert r["total_fp"] == 4, r["total_fp"]  # 4 x2 PBF / 2 AFPh
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "FFMO" not in labels and "FFNAM" not in labels, labels

    # Defensive first fire: not auto-applied (movement unknown), warned.
    r = resolve_attack(s, "57-B2", "57-B3", phase="defensive_first")
    labels = " | ".join(d["label"] for d in r["drm_breakdown"])
    assert "FFMO" not in labels and "FFNAM" not in labels, labels
    assert any("FFNAM" in w for w in r["warnings"]), r["warnings"]

    try:
        resolve_attack(s, "57-B2", "57-B3", phase="rout")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid phase should raise")


# --------------------------------------------------------------------------- #
# (6) Broken / enemy units never fire
# --------------------------------------------------------------------------- #

def test_broken_and_enemy_units_excluded():
    hexes = {
        "57-B2": {"units": [
            _sq("4-6-7 1sq", "German"),
            _sq("4-6-7 2sq", "German", broken=True),
            _sq("4-4-7 1sq", "Russian"),       # enemy infiltrator in the hex
            _sq("MMG", "Russian"),             # abandoned enemy SW
        ], "markers": []},
        "57-B3": {"units": [_sq("4-4-7 2sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert [f["name"] for f in r["firers"]] == ["4-6-7 1sq"], r["firers"]
    assert r["total_fp"] == 8, r["total_fp"]  # 4 x2 PBF
    reasons = {e["name"]: e["reason"] for e in r["excluded"]}
    assert "BROKEN" in reasons["4-6-7 2sq"], reasons
    assert "enemy" in reasons["4-4-7 1sq"], reasons
    assert "enemy" in reasons["MMG"] and "A21" in reasons["MMG"], reasons


def test_prep_fire_marker_excludes_in_prep():
    hexes = {
        "57-B2": {"units": [
            _sq("4-6-7 1sq", "German", markers=["Prep Fire"]),
            _sq("4-6-7 2sq", "German"),
        ], "markers": ["Prep Fire"]},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3", phase="prep")
    assert [f["name"] for f in r["firers"]] == ["4-6-7 2sq"], r["firers"]
    assert any("Prep Fire" in e["reason"] for e in r["excluded"]), r["excluded"]


# --------------------------------------------------------------------------- #
# (7) Ordnance excluded with a warning pointing at the To-Hit process
# --------------------------------------------------------------------------- #

def test_ordnance_excluded_with_warning():
    hexes = {
        "57-B2": {"units": [
            _sq("4-6-7 1sq", "German"),
            _sq("50* MTR", "German"),                 # own mortar: ordnance
            _sq("75* INF obr. 28", "German"),         # own Gun: ordnance
        ], "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["total_fp"] == 8, r["total_fp"]  # squad only, x2 PBF
    ordnance = [e for e in r["excluded"] if "ordnance" in e["reason"]]
    assert len(ordnance) == 2, r["excluded"]
    assert all("To-Hit" in e["reason"] and "C3" in e["reason"]
               for e in ordnance), ordnance


# --------------------------------------------------------------------------- #
# (8) Tool wrapper: no-vsav error path + schema present
# --------------------------------------------------------------------------- #

def test_tool_errors_without_vsav_state():
    out = resolve_attack_tool(firing_hex="57-H9", target_hex="57-H8")
    assert "error" in out and "ift_attack" in out["error"], out
    out = execute_tool("resolve_attack",
                       {"firing_hex": "57-H9", "target_hex": "57-H8"},
                       context=None)
    assert "error" in out and "ift_attack" in out["error"], out
    # Bad hexes with state: ValueError surfaced as a tool error, not a crash.
    out = execute_tool("resolve_attack",
                       {"firing_hex": "57-ZZ99", "target_hex": "57-H8"},
                       context={"vsav_state": _state()})
    assert "error" in out, out


def test_tool_with_vsav_state_via_execute_tool():
    out = execute_tool(
        "resolve_attack",
        {"firing_hex": "57-H9", "target_hex": "57-H8", "phase": "prep"},
        context={"vsav_state": _state()},
    )
    assert out.get("total_fp") == 16 and out.get("drm") == 2, \
        {k: out.get(k) for k in ("total_fp", "drm", "error")}


def test_schema_registered():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "resolve_attack")
    props = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) == {"firing_hex", "target_hex"}
    assert props["phase"]["enum"] == list(attack_resolver.VALID_PHASES)
    # The tool takes hex IDs, never raw FP/DRM.
    assert "fp" not in props and "drm" not in props and "tem" not in props


# --------------------------------------------------------------------------- #
# Extras: leadership and CX derivation
# --------------------------------------------------------------------------- #

def test_known_leader_directs_and_generic_leader_warns():
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German"), _sq("9-1", "German")],
                  "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == -1, r["drm_breakdown"]
    assert any("A7.531" in d["label"] for d in r["drm_breakdown"]), r["drm_breakdown"]
    assert r["cowering"] == "none"  # direction prevents cowering (A7.9)

    hexes["57-B2"]["units"][1] = _sq("geLDR", "German")
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == 0, r["drm_breakdown"]
    assert any("not recoverable" in w for w in r["warnings"]), r["warnings"]
    assert r["cowering"] == "regular"


def test_cx_firer_adds_plus_one():
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German", markers=["CX"])],
                  "markers": ["CX"]},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B3")
    assert r["drm"] == 1, r["drm_breakdown"]
    assert any("A4.51" in d["label"] for d in r["drm_breakdown"]), r["drm_breakdown"]


def test_pinned_firer_halved():
    hexes = {
        "57-B2": {"units": [_sq("4-6-7 1sq", "German", markers=["Pin"])],
                  "markers": ["Pin"]},
        "57-B4": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    }
    r = resolve_attack(_mk_state(hexes), "57-B2", "57-B4")
    assert r["total_fp"] == 2, r["total_fp"]  # 4 / 2 pinned (A7.8), no PBF at range 2


# --------------------------------------------------------------------------- #
# (9) Assault Fire (A7.36) / Spraying Fire (A7.34) capability detection
# --------------------------------------------------------------------------- #

def test_hazmo_h9_advancing_fire_applies_assault_fire():
    """The Finnish 6-4-8 (art fi/fi648S.svg, underscored FP) gets the A7.36
    +1 in the AFPh: 6 ×2 PBF = 12, ÷2 AFPh = 6, +1 Assault Fire = 7; the
    LMG gets no bonus (SW are never Assault Fire): 2 ×2 ÷2 = 2. Total 9 FP
    -> 8 column, still +2 DRM (foxhole)."""
    r = resolve_attack(_state(), "57-H9", "57-H8", phase="advancing")
    assert r["total_fp"] == 9, r["total_fp"]
    assert r["column"] == 8, r["column"]
    assert r["drm"] == 2, r["drm"]

    squad = next(f for f in r["firers"] if f["name"].startswith("6-4-8"))
    lmg = next(f for f in r["firers"] if "LMG" in f["name"])
    assert squad["assault_fire"] is True, squad
    assert squad["fp_resolution"]["final"] == 7, squad["fp_resolution"]
    assert any("assault fire" in s for s in squad["fp_resolution"]["steps"]), squad
    assert any("A7.36" in n for n in squad["notes"]), squad["notes"]
    assert any("counter art" in n for n in squad["notes"]), squad["notes"]
    assert lmg["fp_resolution"]["final"] == 2, lmg["fp_resolution"]
    assert not any("assault" in s for s in lmg["fp_resolution"]["steps"]), lmg

    # Deterministic: the old "not readable from the save" caveat is gone,
    # replaced by an explicit applied-to list.
    assert not any("not readable" in a for a in r["assumptions"]), r["assumptions"]
    assert any("A7.36" in a and "6-4-8 1sq" in a for a in r["assumptions"]), \
        r["assumptions"]
    # No unknown-capability warning for the Finnish squad.
    assert not any("UNKNOWN" in w for w in r["warnings"]), r["warnings"]


def test_hazmo_prep_fire_regression_no_assault_fire():
    """A7.36 is AFPh-only: the existing prep-fire derivation (16 FP +2 DRM)
    must be byte-for-byte free of any assault-fire application."""
    r = resolve_attack(_state(), "57-H9", "57-H8", phase="prep")
    assert r["total_fp"] == 16 and r["drm"] == 2, (r["total_fp"], r["drm"])
    squad = next(f for f in r["firers"] if f["name"].startswith("6-4-8"))
    assert squad["fp_resolution"]["final"] == 12, squad["fp_resolution"]
    assert not any("assault" in s.lower()
                   for s in squad["fp_resolution"]["steps"]), squad
    # Capability is still reported (it's a fact of the counter)...
    assert squad["assault_fire"] is True, squad
    # ...but the A7.36 note appears only in the advancing phase.
    assert not any("A7.36" in n for n in squad["notes"]), squad["notes"]


def test_assault_fire_via_name_fallback_and_long_range_na():
    # American 6-6-6 (Assault Fire by name fallback) adjacent in AFPh:
    # 6 ×2 PBF = 12, ÷2 AFPh = 6, +1 = 7.
    s = _mk_state({
        "57-B2": {"units": [_sq("6-6-6 1sq", "American")], "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    })
    r = resolve_attack(s, "57-B2", "57-B3", phase="advancing")
    assert r["total_fp"] == 7, r["total_fp"]
    # At long range the bonus is NA (A7.36): range 7 > NR 6 ->
    # 6 ÷2 LR = 3, ÷2 AFPh = 1.5, no +1.
    s = _mk_state({
        "57-B2": {"units": [_sq("6-6-6 1sq", "American")], "markers": []},
        "57-B9": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    })
    r = resolve_attack(s, "57-B2", "57-B9", phase="advancing")
    assert r["total_fp"] == 1.5, r["total_fp"]
    assert any("NA at long range" in w and "A7.36" in w
               for w in r["warnings"]), r["warnings"]


def test_no_assault_fire_for_squads_without_capability():
    # German 4-6-7: no underscored FP -> deterministic NO bonus, NO warning.
    s = _two_hex_state("57-B3")
    r = resolve_attack(s, "57-B2", "57-B3", phase="advancing")
    assert r["total_fp"] == 4, r["total_fp"]  # 4 ×2 PBF ÷2 AFPh — unchanged
    squad = r["firers"][0]
    assert squad["assault_fire"] is False, squad
    assert not any("UNKNOWN" in w for w in r["warnings"]), r["warnings"]


def test_unknown_capability_warns_in_advancing_phase_only():
    s = _mk_state({
        "57-B2": {"units": [_sq("4-4-7 1sq", "Chinese")], "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Japanese")], "markers": []},
    })
    r = resolve_attack(s, "57-B2", "57-B3", phase="advancing")
    assert r["firers"][0]["assault_fire"] is None, r["firers"]
    assert r["total_fp"] == 4, r["total_fp"]  # no +1 applied
    assert any("UNKNOWN" in w and "A7.36" in w and "ift_attack" in w
               for w in r["warnings"]), r["warnings"]
    # Same stack in prep fire: capability is irrelevant, no warning.
    r = resolve_attack(s, "57-B2", "57-B3", phase="prep")
    assert not any("UNKNOWN" in w for w in r["warnings"]), r["warnings"]


def test_spraying_fire_surfaced_as_note_never_applied():
    # German 4-6-7 has the underscored range (Spraying Fire) — note within
    # 3 hexes, never an FP change.
    s = _two_hex_state("57-B4")      # range 2
    r = resolve_attack(s, "57-B2", "57-B4", phase="prep")
    squad = r["firers"][0]
    assert squad["spraying_fire"] is True, squad
    assert any("Spraying Fire" in n and "A7.34" in n and "NOT applied" in n
               for n in squad["notes"]), squad["notes"]
    assert r["total_fp"] == 4, r["total_fp"]  # unchanged
    # Beyond 3 hexes (A7.34 max range) the note is irrelevant and absent.
    s = _two_hex_state("57-B6")      # range 4
    r = resolve_attack(s, "57-B2", "57-B6", phase="prep")
    assert not any("Spraying" in n for n in r["firers"][0]["notes"]), \
        r["firers"][0]["notes"]


def test_conditional_capability_note_surfaces_in_afph():
    # SS 6-5-8 (art ge658Ss): no printed underscore, but A25.11 grants
    # Assault Fire in 1944-45 — surfaced as a note, never auto-applied.
    s = _mk_state({
        "57-B2": {"units": [_sq("6-5-8 Esq", "German", art="ge/ge658Ss.svg")],
                  "markers": []},
        "57-B3": {"units": [_sq("4-4-7 1sq", "Russian")], "markers": []},
    })
    r = resolve_attack(s, "57-B2", "57-B3", phase="advancing")
    squad = r["firers"][0]
    assert squad["assault_fire"] is False, squad
    assert r["total_fp"] == 6, r["total_fp"]  # 6 ×2 PBF ÷2 AFPh, no +1
    assert any("A25.11" in n for n in squad["notes"]), squad["notes"]


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
