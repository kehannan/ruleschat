"""
Deterministic ASL Close Combat resolver over parsed .vsav board state.

`resolve_cc(state, hex_id, ...)` takes the dict produced by
`app.services.vsav_service.parse_vsav` and derives — without any LLM in the
loop — a full A11 Close Combat resolution for one hex:

  * which units on each side participate (BROKEN attackers excluded per
    A11.16, SW/ordnance excluded per A11.13),
  * each unit's CC FP with a rule cite (MMC printed FP per the A11.11
    example; SMC — leaders, heroes, commissars — inherent FP 1 per A11.14),
  * the odds ratio rounded DOWN to the printed CCT column and that column's
    black (and Hand-to-Hand red) Kill Number,
  * an itemized, rule-cited CC DRM ledger (leadership A11.141, CX A4.51,
    skiers E4.5, broken defenders A11.16, heroes A15.24),
  * "eliminate on Final DR < KN / Casualty Reduction on Final DR = KN"
    semantics (A11.11) with 2d6 probabilities, including the A7.302 rule
    that CR eliminates a HS/crew outright and wounds a SMC,
  * and — because CC is simultaneous (A11.1) — BOTH directions: the reverse
    (defender-vs-attacker) attack is always computed in the same response,
  * plus explicit Melee-marker notes (Ambush NA per A11.4, withdrawal,
    skiers not locked per A11.15/E4.5) and an `assumptions` list for
    everything the save cannot tell us.

The motivating failure case: the Hazmo fixture's 57-G9 Melee (Russian
2-3-7 HS + commissar + MMG vs Finnish 2-4-8 HS). Asked for the odds and the
DR needed to eliminate the Finn, an LLM hand-derived the CCT math twice and
produced two CONTRADICTORY answers (SMC CC FP claimed as 1 in one and 3 in
the other, both "citing" A11). The correct derivation — 3 FP (2 printed +
1 SMC) vs 2 FP = 3-2 odds, black Kill Number 6, net 0 DRM (the ski
counters in G9 show the "OFF Skis" face: skis are CARRIED, not worn, so
E4.5's ±2 is NA per E4.2/E4.21; commissar direction adds 0), eliminate on
Final DR < 6 with DR = 6 a Casualty Reduction that ALSO eliminates the
half-squad per A7.302 — falls straight out of the parsed state. This
module derives it deterministically so the model presents it instead of
inventing it.

Every rule value below was verified verbatim against the local eASLRB
v3.14 text (static/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf):

  * A11.11: "The FP of attacking units is compared to the FP of those enemy
    units being attacked ... if two 6-2-8 squads combine to attack a 4-6-7
    squad the odds are 12-4, which is then rounded down to the nearest
    corresponding odds ratio printed on the CCT (EX: 7 to 4 would be 3-2;
    11 to 2 would be 4-1; 4 to 15 would be 1-4) ... If the Final DR is <
    the Kill Number listed on the CCT under the applicable odds column, the
    attacked units are eliminated. A Final DR which equals the Kill Number
    ... is a Partial Kill: one (or more) defending unit suffers Casualty
    Reduction as determined by Random Selection. A Final DR > the Kill
    Number has no effect. Normally the black Kill Numbers are used; see
    Hand-to-Hand CC (J2.31) for use of the red Kill Numbers."
  * A11.13: "A SW/ordnance counter may not be used in CC."
  * A11.14: "Any SMC in CC has an inherent FP attack and defense strength
    of one."
  * A11.141: "One leader may direct the CC attack of the unit(s) it defends
    with ... by applying his leadership DRM to the CC DR, in addition to
    adding his inherent FP ... a leader may not use his leadership DRM to
    modify the CC DR of his own attack if he attacks alone."
  * A11.16: "A broken unit may be attacked in CC and is subject to a -2 DRM
    to the CC DR. Broken units may never attack, but still defend with
    their full (unbroken side) FP."
  * A11.19: "The FP of an attacking unit is always halved when attacking a
    concealed Unit in CC."
  * A11.1: "There are no TEM or LOS Hindrance modifications to a CC attack
    DR, nor does PBF/TPBF ever apply to CC. Unlike Fire attacks, CC is
    usually simultaneous, so both sides attack the other even if one or
    both is thereby eliminated."
  * A11.4: Ambush requires advancing into CC ("Whenever Infantry advance
    into CC (unless reinforcing a Melee) ...") and its DRM lasts "until
    that CC becomes a Melee in the next Player Turn"; the chapter-A CC
    chart prints "by Ambush (NA during Melee)".
  * A11.15: Melee locks Infantry "[EXC: bicyclists, skiers]"; A11.71 /
    E4.5: skiers in Melee may leave or change to foot mode in their MPh.
  * A4.51 (CX): "CX units must also add one to any CC attack they make, and
    deduct one from any CC attack made against them."
  * A7.8 (Pin): "The halved FP of a pinned unit in CC applies only to its
    attack, not to its defense."
  * A15.24: "A hero/any FG (even if just another SMC) he is part of ... may
    deduct one from its IFT/CC resolution DR. This DRM is cumulative with
    that of any applicable leadership DRM/additional heroes present."
  * A7.302: "Casualty Reduction eliminates any HS or crew [EXC: Recall;
    D5.341], and wounds any SMC it applies to ... A squad is Reduced to a
    HS."
  * A25.22: "A Commissar is a 9-0 or 10-0 leader" — i.e. leadership DRM 0
    (the rare 8+1 Commissar of A25.224 is OB/CG-only).
  * E4.5: "Skiers engaged in CC must add +2 to their CC Attack DR and are
    subject to a -2 DRM when attacked in CC. However, Skiers in Melee have
    the option in their MPh to leave the Melee or change to foot mode
    (A11.71)."
  * E4.2 (ski mode — who a "Skier" is): "Units on skis are in ski mode and
    are referred to as Skiers. Skiers are identified by placing the
    possessed ski counter with the 'Skis' up."
  * E4.21 (carried skis are NOT ski mode): "When not in ski mode, skis are
    carried atop a unit with the 'OFF Skis' side up at a cost of one PP."
    A unit merely carrying skis is normal Infantry: E4.5's CC DRMs and the
    A11.15 Melee-lock exemption apply to Skiers (ski mode) only.
  * CCT odds columns / Kill Numbers: read from the A11.11 CLOSE COMBAT
    TABLE in the chapter-A charts (black/red): <1-8:0/2 1-8:1/3 1-6:2/4
    1-4:3/5 1-2:4/6 1-1:5/7 3-2:6/8 2-1:7/9 3-1:8/10 4-1:9/11 6-1:10/12
    8-1:11/13 10-1:12/14 >10-1:13/15, "Red Kill Numbers apply to
    Hand-to-Hand CC only."

# ============================================================================
# VERIFY — consolidated list of values/behaviors encoded with less than full
# confidence. Everything else in this module was checked verbatim against
# the eASLRB v3.14 text (quotes above).
#
#  V1. RESOLVED (2026-06-12): worn vs carried is now decoded from the save.
#      The VASL Skis counter's flip layer mirrors E4.2/E4.21 exactly —
#      "Skis" face up (base art) = ski mode/worn, "OFF Skis" face = carried
#      (1 PP) — and parse_vsav exposes it as unit["skis"] ("worn"|
#      "carried"). E4.5's +2/-2 applies ONLY when worn; carried gets an
#      explicit NA ledger line. Residual assumption: a Skis marker whose
#      face cannot be decoded (hand-built states) defaults to worn, the
#      counter's base face.
#  V2. Combined attacks with only SOME units CX (or on skis): A4.51/E4.5 are
#      written per-unit ("any CC attack they make"); how a combined attack
#      mixing CX and non-CX attackers is modified is not stated. We apply
#      the DRM only when ALL attacking units share the state, else warn.
#  V3. Defender-side CX/ski DRM with a mixed defending group: same per-unit
#      ambiguity as V2 — applied only when ALL defenders share the state.
#  V4. Broken defenders mixed with unbroken: per the A11.622 example the -2
#      affects only the broken units' fate within one attack; we itemize it
#      as a conditional line (drm 0) with a warning instead of resolving
#      the attack twice.
#  V5. A Gun crew's possessed Gun is excluded via the SW/ordnance rule
#      (A11.13); vehicles/AFV in CC (CCV process, A11.5/.6, sequential CC
#      A11.31) are NOT modeled — any vehicle-looking counter is excluded
#      with a warning.
#  V6. Unit-type detection for the A7.302 CR note (HS/crew/SMC) parses the
#      counter-name suffix ("1hs"/"Icr"/"1sq"); nonstandard names fall back
#      to "squad" semantics (CR = Reduction) with no special note.
# ============================================================================
"""
import logging
import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from app.asl.attack_resolver import (
    classify_unit,
    ski_state,
    _all_hex_markers,
    _find_hex_key,
)

# ----------------------------------------------------------------------------
# Rule data
# ----------------------------------------------------------------------------

# A11.11 CLOSE COMBAT TABLE (chapter-A charts, eASLRB v3.14).
# (label, minimum odds ratio, black Kill Number, red Kill Number [HtH only]).
# Odds are rounded DOWN to the highest printed column whose ratio does not
# exceed attack/defense (A11.11); anything below 1-8 uses "<1-8".
CCT: List[Tuple[str, Optional[Fraction], int, int]] = [
    ("<1-8", None, 0, 2),
    ("1-8", Fraction(1, 8), 1, 3),
    ("1-6", Fraction(1, 6), 2, 4),
    ("1-4", Fraction(1, 4), 3, 5),
    ("1-2", Fraction(1, 2), 4, 6),
    ("1-1", Fraction(1, 1), 5, 7),
    ("3-2", Fraction(3, 2), 6, 8),
    ("2-1", Fraction(2, 1), 7, 9),
    ("3-1", Fraction(3, 1), 8, 10),
    ("4-1", Fraction(4, 1), 9, 11),
    ("6-1", Fraction(6, 1), 10, 12),
    ("8-1", Fraction(8, 1), 11, 13),
    ("10-1", Fraction(10, 1), 12, 14),
    (">10-1", None, 13, 15),
]

SMC_CC_FP = 1                 # A11.14
LEADERSHIP_RULE = "A11.141"
SW_NA_RULE = "A11.13"
BROKEN_NO_ATTACK_RULE = "A11.16"
BROKEN_DEFENDER_DRM = -2      # A11.16
CX_ATTACK_DRM = 1             # A4.51
CX_DEFENDER_DRM = -1          # A4.51
SKI_ATTACK_DRM = 2            # E4.5
SKI_DEFENDER_DRM = -2         # E4.5
HERO_DRM = -1                 # A15.24

_COMMISSAR_RE = re.compile(r"COM\b")
# Counter-name suffixes: "1hs"/"2hs"/"Ghs", "Icr"/"Acr". No \b before the
# letters — digits/letters give no word boundary ("1hs").
_HS_RE = re.compile(r"hs\b", re.IGNORECASE)
_CREW_RE = re.compile(r"cr\b|crew", re.IGNORECASE)

# 2d6 sum frequencies (out of 36).
_SUM_WAYS = {s: sum(1 for a in range(1, 7) for b in range(1, 7) if a + b == s)
             for s in range(2, 13)}


def _p_le(x: int) -> float:
    """P(2d6 <= x)."""
    return sum(w for s, w in _SUM_WAYS.items() if s <= x) / 36.0


def _p_eq(x: int) -> float:
    """P(2d6 == x)."""
    return _SUM_WAYS.get(x, 0) / 36.0


def _fnum(x: Fraction):
    return int(x) if x.denominator == 1 else float(x)


def cct_column(attack_fp: Fraction, defense_fp: Fraction) -> Dict[str, Any]:
    """Round attack:defense odds DOWN to the printed CCT column (A11.11)."""
    if defense_fp <= 0:
        raise ValueError("CC odds need a positive defense FP.")
    ratio = Fraction(attack_fp) / Fraction(defense_fp)
    chosen = CCT[0]
    for entry in CCT[1:-1]:
        if ratio >= entry[1]:
            chosen = entry
    if ratio > CCT[-2][1]:                       # above 10-1 -> ">10-1"
        chosen = CCT[-1]
    label, _, black, red = chosen
    return {
        "odds": label,
        "kill_number": black,
        "kill_number_hth": red,
        "raw_ratio": f"{_fnum(Fraction(attack_fp))}-{_fnum(Fraction(defense_fp))}",
    }


# ----------------------------------------------------------------------------
# Per-unit CC classification
# ----------------------------------------------------------------------------

def _cc_unit_type(unit: Dict[str, Any], cls: Dict[str, Any]) -> str:
    """'squad' | 'hs' | 'crew' | 'smc' for the A7.302 CR semantics. VERIFY (V6)."""
    if cls["kind"] in ("leader", "hero"):
        return "smc"
    detail = cls.get("detail") or ""
    if _HS_RE.search(detail):
        return "hs"
    if _CREW_RE.search(detail):
        return "crew"
    return "squad"


def _is_commissar(unit: Dict[str, Any]) -> bool:
    return bool(_COMMISSAR_RE.search(unit.get("name") or ""))


# ----------------------------------------------------------------------------
# One direction of the (simultaneous) CC
# ----------------------------------------------------------------------------

def _cc_direction(
    att_units: List[Dict[str, Any]],
    def_units: List[Dict[str, Any]],
    att_side: str,
    def_side: str,
    warnings: List[str],
) -> Dict[str, Any]:
    """Resolve one direction (att_units attack def_units) of the CC."""
    label = f"{att_side} attacks {def_side}"
    excluded: List[Dict[str, Any]] = []
    att_rows: List[Dict[str, Any]] = []
    leaders: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    hero_count = 0

    def _exclude(u, reason):
        excluded.append({"name": u.get("name"), "side": u.get("side"),
                         "reason": reason})

    attack_fp = Fraction(0)
    for u in att_units:
        cls = classify_unit(u)
        markers = u.get("markers") or []
        if u.get("broken"):
            _exclude(u, "BROKEN — broken units may never attack in CC "
                        f"({BROKEN_NO_ATTACK_RULE}); it still DEFENDS with "
                        "its full unbroken-side FP")
            warnings.append(
                f"{u.get('name')} ({att_side}) is broken and cannot attack "
                f"({BROKEN_NO_ATTACK_RULE})."
            )
            continue
        if cls["kind"] in ("sw", "ordnance"):
            _exclude(u, "SW/ordnance counters may not be used in CC "
                        f"({SW_NA_RULE})")
            continue
        if cls["kind"] == "unknown":
            _exclude(u, "unrecognized counter — CC FP not derivable; if this "
                        "is a vehicle, CC vs vehicles uses the CCV process "
                        "(A11.5/A11.31), which is not modeled")  # VERIFY (V5)
            warnings.append(
                f"{u.get('name')} ({att_side}) was not recognized — excluded "
                "from the CC FP totals; verify manually."
            )
            continue

        steps: List[str] = []
        notes: List[str] = []
        if cls["kind"] == "personnel":
            fp = Fraction(cls["fp"])
            steps.append(f"printed FP {_fnum(fp)} (A11.11: CC odds compare "
                         "the units' FP)")
        else:                                    # leader / hero
            fp = Fraction(SMC_CC_FP)
            steps.append("SMC inherent CC FP 1 (A11.14)")
            if cls["kind"] == "leader":
                leaders.append((u, cls))
            else:
                hero_count += 1
        if "Pin" in markers:
            fp = fp / 2
            steps.append("pinned: FP halved for its ATTACK only (A7.8; "
                         "defense unaffected)")
        if u.get("concealed_by"):
            notes.append("concealed attacker: making/directing a CC attack "
                         "forfeits concealment (A11.19); no FP change")
        attack_fp += fp
        att_rows.append({
            "name": u.get("name"), "side": u.get("side"),
            "kind": cls["kind"], "cc_fp": _fnum(fp),
            "steps": steps, "notes": notes, "markers": markers,
        })

    if not att_rows:
        reasons = [e["reason"] for e in excluded]
        return {
            "direction": label,
            "no_attack": True,
            "reason": (f"No eligible {att_side} attackers "
                       f"(exclusions: {reasons})."),
            "excluded": excluded,
        }

    # ---- defense FP (A11.11/.14/.16; SW never count, A11.13) ----
    def_rows: List[Dict[str, Any]] = []
    defense_fp = Fraction(0)
    def_types: List[str] = []
    for u in def_units:
        cls = classify_unit(u)
        if cls["kind"] in ("sw", "ordnance"):
            def_rows.append({
                "name": u.get("name"), "side": u.get("side"), "kind": cls["kind"],
                "cc_fp": 0,
                "steps": [f"SW/ordnance: no CC defense FP ({SW_NA_RULE}); a "
                          "Gun/SW possessed by a unit eliminated in CC may "
                          "itself be eliminated on an Original colored dr of "
                          "1 (A11.13)"],
                "notes": [], "markers": u.get("markers") or [],
            })
            continue
        if cls["kind"] == "unknown":
            def_rows.append({
                "name": u.get("name"), "side": u.get("side"), "kind": "unknown",
                "cc_fp": 0,
                "steps": ["unrecognized counter — defense FP not derivable "
                          "(vehicles defend via the CCV process, A11.5; not "
                          "modeled)"],  # VERIFY (V5)
                "notes": [], "markers": u.get("markers") or [],
            })
            warnings.append(
                f"{u.get('name')} ({def_side}) was not recognized — excluded "
                "from the defense FP; verify manually."
            )
            continue
        steps = []
        notes = []
        if cls["kind"] == "personnel":
            fp = Fraction(cls["fp"])
            steps.append(f"printed FP {_fnum(fp)} (A11.11)")
        else:
            fp = Fraction(SMC_CC_FP)
            steps.append("SMC inherent CC defense strength 1 (A11.14)")
        if u.get("broken"):
            notes.append("broken: still defends with its full unbroken-side "
                         "FP (A11.16)")
        if "Pin" in (u.get("markers") or []):
            notes.append("pinned: defense FP NOT halved — the pin halving "
                         "applies only to its attack (A7.8)")
        if u.get("concealed_by"):
            notes.append("concealed: attacker's FP halved (A11.19); must "
                         "still reveal its Strength Factor before attack "
                         "declaration (A11.19)")
        defense_fp += fp
        def_types.append(_cc_unit_type(u, cls))
        def_rows.append({
            "name": u.get("name"), "side": u.get("side"), "kind": cls["kind"],
            "cc_fp": _fnum(fp), "steps": steps, "notes": notes,
            "markers": u.get("markers") or [],
        })

    if defense_fp <= 0:
        return {
            "direction": label,
            "no_attack": True,
            "reason": (f"No {def_side} unit with a derivable CC defense FP "
                       f"in the Location."),
            "excluded": excluded,
            "defenders": def_rows,
        }

    # ---- attack-level FP halving vs concealment (A11.19) ----
    fp_steps = [f"sum of unit CC FP = {_fnum(attack_fp)}"]
    conc = [bool(u.get("concealed_by")) for u in def_units
            if classify_unit(u)["kind"] in ("personnel", "leader", "hero")]
    if conc and all(conc):
        attack_fp = attack_fp / 2
        fp_steps.append("all defenders concealed: attacking FP halved "
                        f"(A11.19) = {_fnum(attack_fp)}")
    elif any(conc):
        warnings.append(
            f"{def_side} group mixes concealed and unconcealed units: FP is "
            "halved only vs the concealed ones (A11.19) — \"it is rarely "
            "wise to attack both concealed and unconcealed units in the "
            "same CC attack\"; odds below are computed WITHOUT the halving."
        )

    odds = cct_column(attack_fp, defense_fp)

    # ---- DRM ledger ----
    drm_breakdown: List[Dict[str, Any]] = []

    # Leadership (A11.141): one leader may direct, adding his DRM, unless he
    # attacks alone. Commissars are 9-0/10-0 (A25.22) => DRM 0.
    non_leader_attackers = [r for r in att_rows if r["kind"] != "leader"]
    best: Optional[int] = None
    best_name = None
    for u, cls in leaders:
        if "Pin" in (u.get("markers") or []):
            warnings.append(
                f"Leader {u.get('name')} is pinned — A11.4's Ambush drm and "
                "general leader degradation aside, his direction is not "
                "applied."
            )
            continue
        if not non_leader_attackers and len(leaders) == 1:
            warnings.append(
                f"Leader {u.get('name')} attacks alone — he may not apply "
                f"his leadership DRM to his own attack ({LEADERSHIP_RULE})."
            )
            continue
        if _is_commissar(u):
            drm_breakdown.append({
                "label": (f"commissar {u.get('name')} directs: leadership "
                          "DRM 0 — a Commissar is a 9-0 or 10-0 leader "
                          f"(A25.22; the OB-only 8+1 of A25.224 aside), so "
                          f"his direction adds no DRM ({LEADERSHIP_RULE})"),
                "drm": 0,
            })
            continue
        if cls["leadership"] is None:
            warnings.append(
                f"Leader {u.get('name')} is a generic counter — leadership "
                "DRM not recoverable from the save; omitted. If he directs "
                f"the attack, apply his printed DRM ({LEADERSHIP_RULE})."
            )
            continue
        if best is None or cls["leadership"] < best:
            best = cls["leadership"]
            best_name = u.get("name")
    if best is not None:
        if best > 0:
            warnings.append(
                f"Only leader available has a +{best} modifier — direction "
                "is optional, so he is assumed NOT to direct."
            )
        else:
            drm_breakdown.append({
                "label": (f"leadership: {best_name} directs the attack "
                          f"{best:+d} ({LEADERSHIP_RULE})"),
                "drm": best,
            })

    if hero_count:
        drm_breakdown.append({
            "label": (f"heroic DRM {HERO_DRM * hero_count:+d}: "
                      f"{hero_count} hero(es) in the attack, -1 each, "
                      "cumulative with leadership (A15.24)"),
            "drm": HERO_DRM * hero_count,
        })

    def _marker_state(rows_units, marker):
        flags = [marker in (u.get("markers") or []) for u in rows_units]
        return ("all" if flags and all(flags)
                else "some" if any(flags) else "none")

    att_active = [u for u in att_units if not u.get("broken")
                  and classify_unit(u)["kind"] in ("personnel", "leader", "hero")]
    def_active = [u for u in def_units
                  if classify_unit(u)["kind"] in ("personnel", "leader", "hero")]

    # CX (A4.51): +1 by CX attackers, -1 vs CX defenders. VERIFY (V2/V3).
    cx_att = _marker_state(att_active, "CX")
    if cx_att == "all":
        drm_breakdown.append({
            "label": "CX attacker(s): +1 to the CC DR they make (A4.51)",
            "drm": CX_ATTACK_DRM,
        })
    elif cx_att == "some":
        warnings.append(
            "Some (not all) attacking units are CX: A4.51's +1 applies to "
            "CC attacks the CX unit makes — NOT auto-applied to this "
            "combined attack; split the attack or add it manually."
        )
    cx_def = _marker_state(def_active, "CX")
    if cx_def == "all":
        drm_breakdown.append({
            "label": "CX defender(s): -1 to CC attacks made against them "
                     "(A4.51)",
            "drm": CX_DEFENDER_DRM,
        })
    elif cx_def == "some":
        warnings.append(
            "Some (not all) defending units are CX: A4.51's -1 applies to "
            "attacks vs the CX unit(s) — NOT auto-applied."
        )

    # Skiers (E4.5): "Skiers engaged in CC must add +2 to their CC Attack
    # DR and are subject to a -2 DRM when attacked in CC." A Skier is a
    # unit in SKI MODE only — E4.2: "Units on skis are in ski mode and are
    # referred to as Skiers. Skiers are identified by placing the possessed
    # ski counter with the 'Skis' up." — whereas E4.21: "When not in ski
    # mode, skis are carried atop a unit with the 'OFF Skis' side up at a
    # cost of one PP", i.e. a unit CARRYING skis is normal Infantry and
    # takes no E4.5 DRM. ski_state() reads the decoded counter face
    # ("worn"|"carried") from the parsed save. Mixed groups: VERIFY (V2/V3).
    att_ski = [ski_state(u) for u in att_active]
    if att_ski and all(s == "worn" for s in att_ski):
        drm_breakdown.append({
            "label": "attackers are Skiers (ski counter 'Skis' face up, "
                     "E4.2): +2 to their CC Attack DR (E4.5)",
            "drm": SKI_ATTACK_DRM,
        })
    elif "worn" in att_ski:
        warnings.append(
            "Some (not all) attacking units are Skiers (skis worn): E4.5's "
            "+2 is per Skier — NOT auto-applied to this combined attack."
        )
    if "carried" in att_ski and "worn" not in att_ski:
        drm_breakdown.append({
            "label": "attackers' skis are carried (OFF, 1 PP — E4.21), not "
                     "worn: they are not Skiers, so E4.5's +2 CC DRM is NA",
            "drm": 0,
        })
    def_ski = [ski_state(u) for u in def_active]
    if def_ski and all(s == "worn" for s in def_ski):
        drm_breakdown.append({
            "label": "defenders are Skiers (ski counter 'Skis' face up, "
                     "E4.2): -2 to CC attacks made against them (E4.5)",
            "drm": SKI_DEFENDER_DRM,
        })
    elif "worn" in def_ski:
        warnings.append(
            "Some (not all) defending units are Skiers (skis worn): E4.5's "
            "-2 is per Skier — NOT auto-applied."
        )
    if "carried" in def_ski and "worn" not in def_ski:
        drm_breakdown.append({
            "label": "defenders' skis are carried (OFF, 1 PP — E4.21), not "
                     "worn: they are not Skiers, so E4.5's -2 CC DRM is NA",
            "drm": 0,
        })

    # Broken defenders (A11.16): -2. Mixed groups: conditional. VERIFY (V4).
    brk = [bool(u.get("broken")) for u in def_active]
    if brk and all(brk):
        drm_breakdown.append({
            "label": "all defenders broken: -2 to the CC DR (A11.16)",
            "drm": BROKEN_DEFENDER_DRM,
        })
    elif any(brk):
        drm_breakdown.append({
            "label": ("[conditional] -2 vs the BROKEN defender(s) only "
                      "(A11.16): with a mixed group the modified DR affects "
                      "only the broken units' fate (A11.622 example) — NOT "
                      "added to the total"),
            "drm": 0,
        })
        warnings.append(
            f"{def_side} group mixes broken and unbroken units: the -2 "
            "(A11.16) applies only vs the broken ones; consider separate "
            "attacks."
        )

    drm = sum(d["drm"] for d in drm_breakdown)
    kn = odds["kill_number"]

    # ---- outcome semantics (A11.11) ----
    elim_final = kn - 1                 # Final DR < KN eliminates
    elim_orig = kn - 1 - drm
    cr_orig = kn - drm
    result: Dict[str, Any] = {
        "kill_number": kn,
        "eliminate_on": f"Final DR < {kn} (i.e. Final DR <= {elim_final})",
        "casualty_reduction_on": f"Final DR = {kn} (Partial Kill: Random "
                                 "Selection, A11.11)",
        "eliminate_on_original_dr_le": elim_orig,
        "cr_on_original_dr": cr_orig,
        "p_eliminate": round(_p_le(elim_orig), 4),
        "p_casualty_reduction": round(_p_eq(cr_orig), 4),
    }
    result["p_any_effect"] = round(_p_le(elim_orig) + _p_eq(cr_orig), 4)

    cr_notes: List[str] = []
    if def_types and all(t in ("hs", "crew") for t in def_types):
        cr_notes.append(
            "TARGET IS A HALF-SQUAD/CREW: Casualty Reduction eliminates any "
            "HS or crew (A7.302) — so a Final DR EQUAL to the Kill Number "
            f"ALSO eliminates it; effective elimination on Final DR <= {kn} "
            f"(Original DR <= {cr_orig})."
        )
        result["cr_is_elimination"] = True
        result["p_eliminate_including_cr"] = result["p_any_effect"]
    elif def_types and all(t in ("hs", "crew", "smc") for t in def_types):
        cr_notes.append(
            "All defenders are HS/crew/SMC: Casualty Reduction eliminates a "
            "HS/crew outright and WOUNDS a SMC (A7.302, Wound Severity dr "
            "A17.11); Random Selection picks the victim (A11.11)."
        )
    elif "smc" in def_types or "hs" in def_types or "crew" in def_types:
        cr_notes.append(
            "Casualty Reduction semantics vary by defender: squad -> HS; "
            "HS/crew -> eliminated; SMC -> wounded (A7.302); Random "
            "Selection picks the victim (A11.11)."
        )

    return {
        "direction": label,
        "attackers": att_rows,
        "defenders": def_rows,
        "excluded": excluded,
        "attack_fp": _fnum(attack_fp),
        "fp_steps": fp_steps,
        "defense_fp": _fnum(defense_fp),
        "odds_raw": odds["raw_ratio"],
        "odds": odds["odds"],
        "kill_number": kn,
        "kill_number_hth": odds["kill_number_hth"],
        "hth_note": "red (Hand-to-Hand) Kill Number applies ONLY when HtH "
                    "CC is in effect (J2.31, e.g. by SSR/Japanese G1.64) — "
                    "NOT applied here",
        "drm_breakdown": drm_breakdown,
        "drm": drm,
        "result": result,
        "cr_notes": cr_notes,
    }


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

def _matches_filter(unit: Dict[str, Any], filt: str) -> bool:
    """Comma-separated any-substring match on the unit name."""
    name = (unit.get("name") or "").lower()
    return any(part.strip().lower() in name
               for part in filt.split(",") if part.strip())


def resolve_cc(
    state: Dict[str, Any],
    hex_id: str,
    attacker_side: Optional[str] = None,
    attacker_filter: Optional[str] = None,
    defender_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the Close Combat in one hex of a parsed .vsav state.

    Returns an itemized, auditable A11 derivation covering BOTH directions
    (CC is simultaneous, A11.1): per-unit CC FP ledgers with rule cites,
    CCT odds column + Kill Numbers, an itemized CC DRM ledger, elimination/
    Casualty-Reduction thresholds and 2d6 probabilities, plus warnings and
    explicit assumptions. Raises ValueError for unusable inputs (bad hex,
    only one side present). Never mutates `state`.
    """
    if not isinstance(state, dict) or "hexes" not in state:
        raise ValueError("state does not look like a parse_vsav() result.")

    warnings: List[str] = []
    assumptions: List[str] = [
        "No TEM, LOS Hindrance, or PBF/TPBF applies to a CC attack DR "
        "(A11.1) — do not add terrain DRM.",
        "No Ambush status is derived or applied (A11.4): Ambush needs the "
        "Ambush dr made when Infantry advance into CC; the save cannot "
        "reconstruct it.",
        "Hand-to-Hand CC is NOT applied: black Kill Numbers are used "
        "(A11.11); each attack also lists the red HtH Kill Number (J2.31) "
        "for reference only.",
        "Withdrawal from Melee is not modeled: a withdrawing unit may not "
        "attack and suffers -2 on CC attacks against it, +1 per "
        "non-withdrawing friend (A11.2).",
        "CC is simultaneous: both directions below are resolved even if one "
        "side would be eliminated (A11.1) [EXC sequential CC: Ambush, "
        "vehicles, Prisoners — A11.3].",
        "Each direction assumes ALL listed units combine into one attack vs "
        "ALL enemy units; either side may instead split into separate "
        "attacks (A11.12), changing the odds. A SMC cannot be singled out "
        "while stacked with a MMC (A11.14).",
    ]

    key = _find_hex_key(state, hex_id)
    entry = state["hexes"][key]
    units = entry.get("units", [])

    # ---- sides ----
    side_units: Dict[str, List[Dict[str, Any]]] = {}
    for u in units:
        side = u.get("side")
        if not side:
            continue
        side_units.setdefault(side, []).append(u)

    def _has_combatant(us):
        return any(classify_unit(u)["kind"] in ("personnel", "leader", "hero")
                   for u in us)

    combatant_sides = [s for s, us in side_units.items() if _has_combatant(us)]
    if len(combatant_sides) < 2:
        raise ValueError(
            f"Hex {key} does not contain personnel of two opposing sides "
            f"(found: {sorted(side_units)}) — no Close Combat to resolve."
        )

    if attacker_side:
        match = [s for s in combatant_sides
                 if s.lower() == attacker_side.strip().lower()]
        if not match:
            raise ValueError(
                f"attacker_side {attacker_side!r} not present in {key} "
                f"(sides with combat units: {sorted(combatant_sides)})."
            )
        att_side = match[0]
    else:
        # CC is mutual and both directions are reported; the choice only
        # decides which direction is labeled first.
        att_side = max(
            combatant_sides,
            key=lambda s: (sum(1 for u in side_units[s] if not u.get("broken")
                               and classify_unit(u)["kind"] in
                               ("personnel", "leader", "hero")), s),
        )
        assumptions.append(
            f"attacker_side not specified — {att_side} (most Good Order "
            "combat units) listed as the attacker; both directions are "
            "resolved regardless."
        )
    def_sides = [s for s in combatant_sides if s != att_side]
    def_side = def_sides[0]
    if len(def_sides) > 1:
        warnings.append(
            f"Hex {key} holds units of more than two sides "
            f"({sorted(combatant_sides)}); all non-{att_side} units are "
            "treated as one defending force — verify alliances."
        )

    attackers = list(side_units[att_side])
    defenders = [u for s in def_sides for u in side_units[s]]

    filtered_out: List[Dict[str, Any]] = []
    if attacker_filter and attacker_filter.strip():
        kept = [u for u in attackers if _matches_filter(u, attacker_filter)]
        if not kept:
            raise ValueError(
                f"attacker_filter {attacker_filter!r} matches no {att_side} "
                f"unit in {key} "
                f"({[u.get('name') for u in attackers]})."
            )
        for u in attackers:
            if u not in kept:
                filtered_out.append({
                    "name": u.get("name"), "side": u.get("side"),
                    "reason": f"excluded by attacker_filter {attacker_filter!r}",
                })
        attackers = kept
    if defender_filter and defender_filter.strip():
        kept = [u for u in defenders if _matches_filter(u, defender_filter)]
        if not kept:
            raise ValueError(
                f"defender_filter {defender_filter!r} matches no {def_side} "
                f"unit in {key} "
                f"({[u.get('name') for u in defenders]})."
            )
        for u in defenders:
            if u not in kept:
                filtered_out.append({
                    "name": u.get("name"), "side": u.get("side"),
                    "reason": f"excluded by defender_filter {defender_filter!r}",
                })
        defenders = kept

    # ---- Melee marker ----
    melee = "Melee" in _all_hex_markers(entry)
    melee_note = None
    if melee:
        melee_note = (
            "This Location is marked MELEE: the units are locked in from a "
            "previous CCPh (A11.15) and may not move or attack except as "
            "part of CC or Withdrawal (A11.2). Ambush is NA during Melee "
            "(A11.4: its DRM lasts only 'until that CC becomes a Melee'). "
            "Subsequent Melee rounds are resolved exactly like this one — "
            "simultaneous unless a sequential-CC case applies (A11.3)."
        )
        ski_states = {ski_state(u) for u in units} - {None}
        if "worn" in ski_states:
            melee_note += (
                " NOTE: skiers are not locked in Melee (A11.15 EXC) — a "
                "Skier in Melee may leave the Location or change to foot "
                "mode in its MPh (E4.5/A11.71)."
            )
        elif "carried" in ski_states:
            melee_note += (
                " NOTE: the ski counters here show the 'OFF Skis' face — "
                "skis are merely carried (1 PP, E4.21), the units are NOT "
                "Skiers, so the A11.15 Melee-lock exemption for skiers "
                "does not apply; they are locked in Melee like any other "
                "Infantry."
            )
    else:
        assumptions.append(
            "No Melee marker in the hex: if this CC follows an advance into "
            "the Location this CCPh, an Ambush dr would be made first when "
            "advancing into a woods/building Location or with/against "
            "concealed units (A11.4) — not modeled."
        )

    forward = _cc_direction(attackers, defenders, att_side, def_side,
                            warnings)
    reverse = _cc_direction(defenders, attackers, def_side, att_side,
                            warnings)
    if filtered_out:
        forward.setdefault("excluded", []).extend(
            e for e in filtered_out if e["side"] == att_side)
        reverse.setdefault("excluded", []).extend(
            e for e in filtered_out if e["side"] != att_side)

    if forward.get("no_attack") and reverse.get("no_attack"):
        raise ValueError(
            f"Neither side in {key} can make a CC attack: "
            f"{forward.get('reason')} / {reverse.get('reason')}"
        )

    out = {
        "hex": key,
        "melee": melee,
        "melee_note": melee_note,
        "attacker_side": att_side,
        "defender_side": def_side,
        "attacks": [forward, reverse],
        "warnings": warnings,
        "assumptions": assumptions,
    }
    logging.info(
        "⚔️ resolve_cc(%s): %s %s FP vs %s FP -> %s (KN %s, DRM %+d) | "
        "reverse %s",
        key, att_side,
        forward.get("attack_fp"), forward.get("defense_fp"),
        forward.get("odds"), forward.get("kill_number"),
        forward.get("drm") or 0, reverse.get("odds"),
    )
    return out
