#!/usr/bin/env python
"""
Tests for the deterministic Close Combat resolver (app/asl/cc_resolver.py)
and its agentic tool wrapper.

Anchored on the real failure case: the Hazmo fixture's 57-G9 Melee (Russian
2-3-7 HS + commissar + MMG vs Finnish 2-4-8 HS; both sides' ski counters
show the "OFF Skis" face — skis CARRIED, not worn). Asked for the odds and
the DR needed to eliminate the Finn, an LLM hand-derived the CCT math twice
and gave two CONTRADICTORY answers (SMC CC FP claimed as 1 in one, 3 in the
other, both "citing" A11). Correct per the eASLRB v3.14 text:
3 FP (2 printed + 1 SMC, A11.11/A11.14; the MMG adds nothing, A11.13) vs
2 FP = 3-2 odds, black Kill Number 6 (A11.11 CCT), net 0 DRM — and NOT as
canceling ski DRMs: skis carried at 1 PP (E4.21) make no one a Skier
(E4.2), so E4.5's +2/-2 is NA in both directions (explicit drm-0 ledger
lines); commissar leadership 0, A25.22 — eliminate on Final DR < 6, and a
Final DR of exactly 6 is a Casualty Reduction that ALSO eliminates the
half-squad (A7.302). The resolver must produce that from the parsed save.

Runnable directly (`python tests/test_cc_resolver.py`) or under pytest.
No network, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import cc_resolver
from app.asl.cc_resolver import cct_column, resolve_cc
from app.asl.tools import TOOL_SCHEMAS, CONTEXT_TOOLS, execute_tool
from app.asl.tools import resolve_cc as resolve_cc_tool
from app.services.vsav_service import parse_vsav

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"

_STATE = None


def _state():
    global _STATE
    if _STATE is None:
        _STATE = parse_vsav(FIXTURE)
    return _STATE


def _mk_state(units, markers=None):
    """Minimal synthetic parse_vsav-shaped state: one CC hex on board 57."""
    return {
        "hexes": {"57-B2": {"units": units, "markers": list(markers or [])}},
        "boards": [{
            "name": "57", "base": "57", "slot": [0, 0], "version": None,
            "reversed": False, "crop": {"x": 0, "y": 0, "w": -1, "h": -1},
            "ssr_transforms": [],
        }],
    }


def _u(name, side, markers=None, **kw):
    u = {"name": name, "side": side}
    if markers:
        u["markers"] = list(markers)
    u.update(kw)
    return u


def _dirs(r):
    """(forward, reverse) attack dicts."""
    return r["attacks"][0], r["attacks"][1]


# --------------------------------------------------------------------------- #
# (1) THE headline regression: the 57-G9 Melee, Russian HS + commissar
#     vs Finnish HS — values verified against eASLRB v3.14 A11 / CCT / E4.5
# --------------------------------------------------------------------------- #

def test_g9_melee_russian_attack_is_3_to_2_kill_number_6():
    r = resolve_cc(_state(), "57-G9", attacker_side="Russian")
    fwd, rev = _dirs(r)

    assert r["attacker_side"] == "Russian" and r["defender_side"] == "Finnish"
    assert fwd["direction"] == "Russian attacks Finnish", fwd["direction"]

    # CC FP ledger: HS printed FP 2 (A11.11) + commissar SMC FP 1 (A11.14).
    # NOT 1 total and NOT 3 for the SMC — the two contradictory LLM answers.
    rows = {a["name"]: a for a in fwd["attackers"]}
    assert rows["2-3-7 1hs"]["cc_fp"] == 2, rows
    assert rows["ruCOM"]["cc_fp"] == 1, rows
    assert any("A11.14" in s for s in rows["ruCOM"]["steps"]), rows["ruCOM"]
    assert fwd["attack_fp"] == 3, fwd["attack_fp"]

    # The MMG must NOT add CC FP (A11.13).
    mmg = [e for e in fwd["excluded"] if e["name"] == "MMG"]
    assert mmg and "A11.13" in mmg[0]["reason"], fwd["excluded"]

    # Defense: Finnish 2-4-8 HS printed FP 2 -> odds 3-2, black KN 6.
    assert fwd["defense_fp"] == 2, fwd["defense_fp"]
    assert fwd["odds"] == "3-2" and fwd["kill_number"] == 6, \
        (fwd["odds"], fwd["kill_number"])

    # DRM: every ski counter in G9 shows the "OFF Skis" face — skis are
    # CARRIED (1 PP, E4.21), no one is a Skier (E4.2), so E4.5's +2/-2 is
    # NA. The ledger must say so explicitly (drm-0 lines, both sides), NOT
    # apply a canceling +2/-2 pair. Commissar direction adds 0 (a Commissar
    # is a 9-0 or 10-0 leader, A25.22). Net 0.
    ski = [d for d in fwd["drm_breakdown"] if "E4.5" in d["label"]]
    assert ski, fwd["drm_breakdown"]
    assert all(d["drm"] == 0 for d in ski), fwd["drm_breakdown"]
    assert all("carried" in d["label"] and "NA" in d["label"]
               and "1 PP" in d["label"] for d in ski), ski
    att_na = [d for d in ski if "+2" in d["label"]]
    def_na = [d for d in ski if "-2" in d["label"]]
    assert att_na and def_na, ski
    com = [d for d in fwd["drm_breakdown"] if "commissar" in d["label"]]
    assert com and com[0]["drm"] == 0 and "A25.22" in com[0]["label"], \
        fwd["drm_breakdown"]
    assert fwd["drm"] == 0, fwd["drm_breakdown"]
    # The reverse direction carries its own NA notes too.
    rev_ski = [d for d in rev["drm_breakdown"] if "E4.5" in d["label"]]
    assert rev_ski and all(d["drm"] == 0 and "carried" in d["label"]
                           for d in rev_ski), rev["drm_breakdown"]

    # Eliminate on Final DR < 6; DR = 6 is CR — which ELIMINATES a HS
    # (A7.302), so the Finn dies on a Final (= Original, net 0 DRM) DR <= 6.
    res = fwd["result"]
    assert res["eliminate_on_original_dr_le"] == 5, res
    assert res["cr_on_original_dr"] == 6, res
    assert res.get("cr_is_elimination") is True, res
    assert abs(res["p_eliminate"] - 10 / 36) < 1e-3, res
    assert abs(res["p_eliminate_including_cr"] - 15 / 36) < 1e-3, res
    assert any("A7.302" in n for n in fwd["cr_notes"]), fwd["cr_notes"]

    # Reverse direction in the SAME response: Finn 2 FP vs HS+SMC defense 3
    # -> 2-3 rounds DOWN to 1-2 (A11.11), black KN 4.
    assert rev["direction"] == "Finnish attacks Russian", rev["direction"]
    assert rev["attack_fp"] == 2 and rev["defense_fp"] == 3, rev
    assert rev["odds_raw"] == "2-3" and rev["odds"] == "1-2", rev
    assert rev["kill_number"] == 4 and rev["drm"] == 0, rev

    # Melee marker: noted, with Ambush NA. Skis are CARRIED here, so the
    # A11.15 skier Melee-lock exemption must NOT be offered — instead the
    # note says the units are locked like any other Infantry (E4.21).
    assert r["melee"] is True
    assert "Ambush is NA" in r["melee_note"], r["melee_note"]
    assert "A11.15" in r["melee_note"], r["melee_note"]
    assert "skiers are not locked" not in r["melee_note"], r["melee_note"]
    assert "carried" in r["melee_note"], r["melee_note"]
    assert "locked in Melee like any other Infantry" in r["melee_note"], \
        r["melee_note"]

    # Core assumptions always present.
    assert any("no tem" in a.lower() for a in r["assumptions"]), r["assumptions"]
    assert any("Hand-to-Hand" in a for a in r["assumptions"]), r["assumptions"]
    assert any("simultaneous" in a for a in r["assumptions"]), r["assumptions"]


def test_g9_attacker_side_inferred_when_omitted():
    r = resolve_cc(_state(), "57-G9")
    # Russian has 2 GO combat units (HS + commissar) vs Finland's 1.
    assert r["attacker_side"] == "Russian", r["attacker_side"]
    assert any("attacker_side not specified" in a for a in r["assumptions"]), \
        r["assumptions"]
    assert len(r["attacks"]) == 2


def test_g9_attacker_filter_restricts_units():
    # Only the HS attacks (no commissar): 2 vs 2 = 1-1, black KN 5.
    r = resolve_cc(_state(), "57-G9", attacker_side="Russian",
                   attacker_filter="2-3-7")
    fwd, rev = _dirs(r)
    assert fwd["attack_fp"] == 2 and fwd["odds"] == "1-1", fwd
    assert fwd["kill_number"] == 5, fwd
    assert any("attacker_filter" in e["reason"] for e in fwd["excluded"]), \
        fwd["excluded"]
    # Reverse keeps the filtered group as the (smaller) defending force.
    assert rev["defense_fp"] == 2, rev


# --------------------------------------------------------------------------- #
# (2) CCT columns: the A11.11 rounding examples, verbatim from the rule
# --------------------------------------------------------------------------- #

def test_cct_rounding_matches_a11_11_examples():
    # "if two 6-2-8 squads combine to attack a 4-6-7 squad the odds are 12-4"
    assert cct_column(12, 4)["odds"] == "3-1"
    # "7 to 4 would be 3-2; 11 to 2 would be 4-1; 4 to 15 would be 1-4"
    assert cct_column(7, 4)["odds"] == "3-2"
    assert cct_column(11, 2)["odds"] == "4-1"
    assert cct_column(4, 15)["odds"] == "1-4"
    # A11.141 example: 1-2 (5-8) eliminates on Final DR < 4 -> KN 4;
    # 3-2 (8-5) eliminates on DR < 6 -> KN 6.
    assert cct_column(5, 8) == {"odds": "1-2", "kill_number": 4,
                                "kill_number_hth": 6, "raw_ratio": "5-8"}
    assert cct_column(8, 5)["kill_number"] == 6
    # Chart edges: <1-8 / exact columns / >10-1.
    assert cct_column(1, 9)["odds"] == "<1-8"
    assert cct_column(1, 9)["kill_number"] == 0
    assert cct_column(1, 8)["odds"] == "1-8"
    assert cct_column(10, 1)["odds"] == "10-1"
    assert cct_column(11, 1)["odds"] == ">10-1"
    assert cct_column(11, 1)["kill_number"] == 13
    # Red (HtH) kill numbers are black + 2 across the chart.
    for _, _, black, red in cc_resolver.CCT:
        assert red == black + 2


# --------------------------------------------------------------------------- #
# (3) Broken units: never attack (excluded + warned), defend full FP at -2
# --------------------------------------------------------------------------- #

def test_broken_attacker_excluded_and_defends_full_fp():
    units = [
        _u("4-4-7 1sq", "Russian", broken=True),
        _u("4-4-7 2sq", "Russian"),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, rev = _dirs(r)
    # Broken squad may not attack (A11.16): only the GO squad's 4 FP.
    assert fwd["attack_fp"] == 4, fwd
    brk = [e for e in fwd["excluded"] if e["name"] == "4-4-7 1sq"]
    assert brk and "A11.16" in brk[0]["reason"], fwd["excluded"]
    assert any("broken" in w.lower() and "A11.16" in w
               for w in r["warnings"]), r["warnings"]
    # But it DEFENDS with full unbroken-side FP in the reverse direction:
    # German 4 vs 4+4=8 -> 1-2.
    assert rev["defense_fp"] == 8 and rev["odds"] == "1-2", rev
    drow = next(d for d in rev["defenders"] if d["name"] == "4-4-7 1sq")
    assert any("full unbroken-side FP" in n for n in drow["notes"]), drow
    # Mixed broken/unbroken defenders: conditional -2 line, not totaled.
    cond = [d for d in rev["drm_breakdown"] if "A11.16" in d["label"]]
    assert cond and cond[0]["drm"] == 0, rev["drm_breakdown"]
    assert rev["drm"] == 0, rev


def test_all_defenders_broken_gets_minus_2():
    units = [
        _u("4-4-7 1sq", "Russian", broken=True),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="German")
    fwd, rev = _dirs(r)
    assert fwd["drm"] == -2, fwd["drm_breakdown"]
    assert any("A11.16" in d["label"] for d in fwd["drm_breakdown"]), fwd
    # The broken side cannot attack at all — but the response still carries
    # both directions, the reverse as an explicit no_attack.
    assert rev.get("no_attack") is True, rev
    assert "A11.16" in str(rev["excluded"]), rev


# --------------------------------------------------------------------------- #
# (4) HS / SMC Casualty Reduction semantics (A7.302)
# --------------------------------------------------------------------------- #

def test_hs_target_cr_equals_elimination():
    units = [
        _u("4-6-7 1sq", "German"),
        _u("2-3-7 1hs", "Russian"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="German")
    fwd, _ = _dirs(r)
    # 4 vs 2 = 2-1, KN 7: eliminate on <=6, CR (=7) also eliminates the HS.
    assert fwd["odds"] == "2-1" and fwd["kill_number"] == 7, fwd
    res = fwd["result"]
    assert res.get("cr_is_elimination") is True, res
    assert res["eliminate_on_original_dr_le"] == 6, res
    assert abs(res["p_eliminate_including_cr"]
               - res["p_any_effect"]) < 1e-9, res
    assert any("HALF-SQUAD" in n and "A7.302" in n for n in fwd["cr_notes"]), \
        fwd["cr_notes"]


def test_smc_and_squad_mixed_cr_note():
    units = [
        _u("4-6-7 1sq", "German"),
        _u("4-4-7 1sq", "Russian"),
        _u("9-1", "Russian"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="German")
    fwd, rev = _dirs(r)
    # Defense 4 + 1 (SMC, A11.14) = 5 -> 4 vs 5 rounds down to 1-2.
    assert fwd["defense_fp"] == 5 and fwd["odds"] == "1-2", fwd
    assert fwd["result"].get("cr_is_elimination") is None, fwd["result"]
    assert any("squad -> HS" in n for n in fwd["cr_notes"]), fwd["cr_notes"]
    # Reverse: the 9-1 leader directs (A11.141) AND adds 1 FP: 5 vs 4 = 1-1.
    assert rev["attack_fp"] == 5 and rev["odds"] == "1-1", rev
    lead = [d for d in rev["drm_breakdown"] if "A11.141" in d["label"]]
    assert lead and lead[0]["drm"] == -1, rev["drm_breakdown"]
    assert rev["drm"] == -1, rev


def test_leader_alone_cannot_direct_own_attack():
    units = [_u("9-1", "Russian"), _u("4-6-7 1sq", "German")]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, _ = _dirs(r)
    assert fwd["attack_fp"] == 1, fwd          # SMC FP 1 (A11.14)
    assert fwd["odds"] == "1-4", fwd           # 1 vs 4
    assert fwd["drm"] == 0, fwd["drm_breakdown"]
    assert any("alone" in w and "A11.141" in w for w in r["warnings"]), \
        r["warnings"]


# --------------------------------------------------------------------------- #
# (5) Pin / concealment / CX
# --------------------------------------------------------------------------- #

def test_pinned_attacker_halved_but_defense_unaffected():
    units = [
        _u("4-4-7 1sq", "Russian", markers=["Pin"]),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, rev = _dirs(r)
    assert fwd["attack_fp"] == 2, fwd          # 4 halved (A7.8)
    assert fwd["odds"] == "1-2", fwd
    arow = fwd["attackers"][0]
    assert any("A7.8" in s for s in arow["steps"]), arow
    # Defense side of the pinned unit is NOT halved (A7.8).
    assert rev["defense_fp"] == 4, rev
    drow = rev["defenders"][0]
    assert any("NOT halved" in n for n in drow["notes"]), drow


def test_concealed_defenders_halve_attacking_fp():
    units = [
        _u("4-6-7 1sq", "German"),
        _u("4-4-7 1sq", "Russian", concealed_by="?"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="German")
    fwd, rev = _dirs(r)
    assert fwd["attack_fp"] == 2, fwd          # 4 halved vs concealed (A11.19)
    assert any("A11.19" in s for s in fwd["fp_steps"]), fwd["fp_steps"]
    assert fwd["odds"] == "1-2", fwd           # 2 vs 4
    # The concealed unit's own attack is full FP, with the forfeit note.
    assert rev["attack_fp"] == 4, rev
    assert any("forfeits concealment" in n
               for n in rev["attackers"][0]["notes"]), rev["attackers"]


def test_cx_drm_both_directions():
    units = [
        _u("4-4-7 1sq", "Russian", markers=["CX"]),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, rev = _dirs(r)
    # CX attacker: +1 to the CC attack it makes (A4.51).
    cx = [d for d in fwd["drm_breakdown"] if "A4.51" in d["label"]]
    assert cx and cx[0]["drm"] == 1 and fwd["drm"] == 1, fwd["drm_breakdown"]
    # CX defender: -1 to CC attacks made against it (A4.51).
    cx = [d for d in rev["drm_breakdown"] if "A4.51" in d["label"]]
    assert cx and cx[0]["drm"] == -1 and rev["drm"] == -1, rev["drm_breakdown"]


# --------------------------------------------------------------------------- #
# (5b) Skis: E4.5's +2/-2 applies to Skiers (worn, E4.2) only — carried
#      skis (E4.21) are 1 PP of baggage, and a bare "Skis" marker without a
#      decoded face defaults to worn (the counter's base face)
# --------------------------------------------------------------------------- #

def test_worn_skiers_get_e45_drm_both_directions():
    units = [
        _u("4-4-7 1sq", "Russian", skis="worn"),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units, markers=["Melee"]), "57-B2",
                   attacker_side="Russian")
    fwd, rev = _dirs(r)
    # Skier attacker: +2 to the CC Attack DR it makes (E4.5).
    ski = [d for d in fwd["drm_breakdown"] if "E4.5" in d["label"]]
    assert ski and ski[0]["drm"] == 2 and fwd["drm"] == 2, fwd["drm_breakdown"]
    assert "Skiers" in ski[0]["label"] and "E4.2" in ski[0]["label"], ski
    # Skier defender: -2 to CC attacks made against it (E4.5).
    ski = [d for d in rev["drm_breakdown"] if "E4.5" in d["label"]]
    assert ski and ski[0]["drm"] == -2 and rev["drm"] == -2, \
        rev["drm_breakdown"]
    # Worn skis DO get the A11.15 Melee-lock exemption note.
    assert "skiers are not locked" in r["melee_note"], r["melee_note"]


def test_carried_skis_get_na_ledger_note_not_drm():
    units = [
        _u("4-4-7 1sq", "Russian", skis="carried"),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, rev = _dirs(r)
    for d_, sign in ((fwd, "+2"), (rev, "-2")):
        na = [d for d in d_["drm_breakdown"] if "E4.5" in d["label"]]
        assert na and na[0]["drm"] == 0, d_["drm_breakdown"]
        assert "carried" in na[0]["label"] and "1 PP" in na[0]["label"], na
        assert "NA" in na[0]["label"] and sign in na[0]["label"], na
        assert d_["drm"] == 0, d_["drm_breakdown"]


def test_bare_skis_marker_defaults_to_worn():
    # Hand-built state with only a "Skis" marker (no decoded face): the
    # counter's base face is the "Skis" side, so treat it as worn.
    units = [
        _u("4-4-7 1sq", "Russian", markers=["Skis"]),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, _ = _dirs(r)
    ski = [d for d in fwd["drm_breakdown"] if "E4.5" in d["label"]]
    assert ski and ski[0]["drm"] == 2, fwd["drm_breakdown"]


def test_mixed_worn_and_carried_attackers_warn_not_apply():
    units = [
        _u("4-4-7 1sq", "Russian", skis="worn"),
        _u("4-4-7 2sq", "Russian", skis="carried"),
        _u("4-6-7 1sq", "German"),
    ]
    r = resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian")
    fwd, _ = _dirs(r)
    # No auto-applied ski DRM; a per-Skier warning instead (V2).
    assert not any("E4.5" in d["label"] for d in fwd["drm_breakdown"]), \
        fwd["drm_breakdown"]
    assert fwd["drm"] == 0, fwd["drm_breakdown"]
    assert any("Some (not all) attacking units are Skiers" in w
               for w in r["warnings"]), r["warnings"]


# --------------------------------------------------------------------------- #
# (6) Errors and edge shapes
# --------------------------------------------------------------------------- #

def test_one_sided_hex_raises():
    units = [_u("4-4-7 1sq", "Russian"), _u("MMG", "German")]  # SW, no enemy unit
    try:
        resolve_cc(_mk_state(units), "57-B2")
    except ValueError as e:
        assert "two opposing sides" in str(e), e
    else:
        raise AssertionError("hex without two combatant sides should raise")


def test_bad_attacker_side_and_bad_filter_raise():
    units = [_u("4-4-7 1sq", "Russian"), _u("4-6-7 1sq", "German")]
    try:
        resolve_cc(_mk_state(units), "57-B2", attacker_side="Finnish")
    except ValueError as e:
        assert "not present" in str(e), e
    else:
        raise AssertionError("unknown attacker_side should raise")
    try:
        resolve_cc(_mk_state(units), "57-B2", attacker_side="Russian",
                   attacker_filter="9-2")
    except ValueError as e:
        assert "matches no" in str(e), e
    else:
        raise AssertionError("filter matching nothing should raise")


def test_no_melee_marker_mentions_ambush_assumption():
    units = [_u("4-4-7 1sq", "Russian"), _u("4-6-7 1sq", "German")]
    r = resolve_cc(_mk_state(units), "57-B2")
    assert r["melee"] is False and r["melee_note"] is None
    assert any("Ambush dr" in a for a in r["assumptions"]), r["assumptions"]


# --------------------------------------------------------------------------- #
# (7) Tool wrapper: schema, context plumbing, no-save error path
# --------------------------------------------------------------------------- #

def test_tool_errors_without_vsav_state():
    out = resolve_cc_tool(hex_id="57-G9")
    assert "error" in out, out
    # No fallback CC tool exists: the model must derive with citations and
    # mention that a save would give exact numbers.
    assert "no" in out["error"].lower() and "A11.14" in out["error"], out
    assert ".vsav" in out["error"], out
    out = execute_tool("resolve_cc", {"hex_id": "57-G9"}, context=None)
    assert "error" in out, out
    # Bad hex with a state: ValueError surfaced as a tool error, not a crash.
    out = execute_tool("resolve_cc", {"hex_id": "57-ZZ99"},
                       context={"vsav_state": _state()})
    assert "error" in out, out


def test_tool_with_vsav_state_via_execute_tool():
    out = execute_tool(
        "resolve_cc",
        {"hex_id": "57-G9", "attacker_side": "Russian"},
        context={"vsav_state": _state()},
    )
    fwd = out["attacks"][0]
    assert fwd["attack_fp"] == 3 and fwd["odds"] == "3-2", fwd
    assert fwd["kill_number"] == 6 and fwd["drm"] == 0, fwd


def test_schema_registered_and_context_tool():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "resolve_cc")
    props = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) == {"hex_id"}
    assert set(props) == {"hex_id", "attacker_side", "attacker_filter",
                          "defender_filter"}
    # The tool takes a hex ID, never raw FP/odds/DRM numbers.
    assert "fp" not in props and "drm" not in props and "odds" not in props
    assert "resolve_cc" in CONTEXT_TOOLS


def test_cc_attack_no_save_matches_a11_examples():
    r = execute_tool("cc_attack", {"attack_fp": 8, "defense_fp": 4,
                                   "defender_types": ["squad"]})
    assert r["odds"] == "2-1" and r["kill_number"] == 7, r
    r2 = execute_tool("cc_attack", {"attack_fp": 6, "defense_fp": 4})
    assert r2["odds"] == "3-2" and r2["kill_number"] == 6, r2


def test_cc_attack_leader_drm_shifts_threshold():
    # 6 vs 4 -> 3-2, KN 6; a -1 leadership DRM eases the kill.
    r = execute_tool("cc_attack", {"attack_fp": 6, "defense_fp": 4, "drm": -1})
    assert r["drm"] == -1 and r["kill_number"] == 6, r
    # Final DR < KN eliminates (A11.11): KN6, drm -1 -> Original <= 6 eliminates.
    assert r["eliminate_on_original_dr_le"] == 6, r
    assert r["cr_on_original_dr"] == 7, r


def test_cc_attack_other_drm_summed_and_hs_cr_is_elim():
    r = execute_tool("cc_attack", {
        "attack_fp": 6, "defense_fp": 3, "defender_types": ["hs"],
        "other_drm": [{"label": "leader", "drm": -1}, {"label": "CX", "drm": 1}],
    })
    assert r["drm"] == 0, r                      # -1 + 1
    assert r.get("cr_is_elimination") is True, r


def test_cc_attack_schema_registered_not_context_tool():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "cc_attack")
    assert set(schema["parameters"]["required"]) == {"attack_fp", "defense_fp"}
    assert "cc_attack" not in CONTEXT_TOOLS


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
