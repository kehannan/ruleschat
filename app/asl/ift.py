"""
Infantry Fire Table (IFT) probability engine — deterministic, no LLM.

Given an FP column, a DRM, and whether cowering applies, enumerate all 36
two-die combinations and return the probability of each combat result.

Cowering: ASL resolves the attack on a 2-die DR. When the two dice come up
doubles and cowering applies, the FP column shifts one to the LEFT (weaker)
before the result is read. Doubles is a property of the individual dice, so
we enumerate all 36 ordered combinations rather than the 11 possible sums.

Two entry points:

- `compute_distribution(column, drm, ...)` — the original "quick odds" engine:
  the caller has already done the rules work of finding the FP column and DRM.
- `compute_attack(units, ...)` — the attack builder: resolves per-unit
  firepower modification (A7.2–.36), assembles an itemized DRM ledger
  (A7.6, A4.6), auto-derives cowering (A7.9), runs the distribution, and
  optionally convolves it with target morale-check / vehicle-kill math
  (A7.301–.308, A7.8) to give headline break/pin/casualty odds.
  See docs/ift_attack_tool_plan.md for the full design.
"""

import json
import math
import re
from fractions import Fraction
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, List, Optional

_TABLE_PATH = Path(__file__).with_name("ift_table.json")

_TABLE: Dict[str, Any] | None = None


def _load_table() -> Dict[str, Any]:
    global _TABLE
    if _TABLE is None:
        with open(_TABLE_PATH, "r", encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def valid_columns() -> List[int]:
    """The selectable FP columns, e.g. [1, 2, 4, 6, 8, 12, 16, 20, 24, 30, 36]."""
    return list(_load_table()["columns"])


# Cowering shifts the FP column left on a doubles DR. Conscripts etc. "double
# cower" (two columns). Shifting off the left edge of the table = no attack.
COWERING_SHIFT = {"none": 0, "regular": 1, "double": 2}


def _dr_row_key(final_dr: int, dr_rows: List[str]) -> str:
    """Clamp a final DR to a table row key. '0' is the ≤0 row, '15' the ≥15 row."""
    lo = int(dr_rows[0])     # 0  (≤0)
    hi = int(dr_rows[-1])    # 15 (≥15)
    if final_dr <= lo:
        return dr_rows[0]
    if final_dr >= hi:
        return dr_rows[-1]
    return str(final_dr)


def _sniper_probabilities(san: int) -> Dict[str, Any]:
    """
    Sniper-check odds for an enemy SAN.

    A sniper check triggers when the original (unmodified) 2d6 total equals
    the enemy SAN; a separate 1d6 then resolves it — 1 = big sniper,
    2 = little sniper. So a sniper (big OR little) fires with probability
    P(2d6 = SAN) × 2/6, independent of the IFT FP / DRM / cowering.
    """
    san_count = sum(1 for d1 in range(1, 7) for d2 in range(1, 7) if d1 + d2 == san)
    p_trigger = san_count / 36
    return {
        "san": san,
        "p_trigger": round(p_trigger, 4),
        "p_sniper": round(p_trigger * 2 / 6, 4),
    }


def compute_distribution(
    column: int, drm: int = 0, cowering: str = "none", san: int | None = None
) -> Dict[str, Any]:
    """
    Probability of each IFT result for the given attack.

    Args:
        column: FP column — must be one of `valid_columns()`.
        drm: Total DR modifier (negative is favorable to the firer).
        cowering: "none", "regular" (doubles shift 1 column left), or "double"
                  (doubles shift 2 columns left, e.g. Conscripts). When the
                  shift moves left of the 1 FP column the attack falls off the
                  table → no effect.
        san: Enemy Sniper Activation Number (2–12), or None to skip the sniper
             calc. When set, the result includes a `sniper` block.

    Returns a dict:
        {
          "column": 16,
          "drm": 2,
          "cowering": "regular",
          "distribution": [
             {"result": "NMC", "probability": 0.1389, "count": 5},  # count out of 36
             ...
          ],
          "cowering_outcomes": 6,   # how many of the 36 combos cowered (0 if "none")
        }
    Distribution is sorted by descending probability.
    """
    table = _load_table()
    columns: List[int] = table["columns"]
    dr_rows: List[str] = table["dr_rows"]
    results: Dict[str, List[str]] = table["results"]
    no_effect: str = table.get("no_effect", "—")

    if column not in columns:
        raise ValueError(
            f"Invalid FP column {column!r}. Must be one of {columns}."
        )
    if cowering not in COWERING_SHIFT:
        raise ValueError(
            f"Invalid cowering mode {cowering!r}. Must be one of {list(COWERING_SHIFT)}."
        )
    if san is not None and not (2 <= san <= 12):
        raise ValueError(f"Invalid SAN {san!r}. Must be between 2 and 12.")

    base_idx = columns.index(column)
    shift = COWERING_SHIFT[cowering]
    tally: Dict[str, int] = defaultdict(int)
    # Per-cell occurrence: (column_value, dr_row_key) -> count. Lets the UI
    # paint a heatmap of where the dice land inside the actual IFT grid.
    cell_counts: Dict[tuple, int] = defaultdict(int)
    active_columns: set = set()
    off_table_count = 0
    cowering_count = 0

    # Enumerate all 36 ordered dice combinations (each equally likely, 1/36).
    for d1 in range(1, 7):
        for d2 in range(1, 7):
            is_doubles = (d1 == d2)
            final_dr = d1 + d2 + drm

            col_idx = base_idx
            if shift and is_doubles:
                cowering_count += 1
                col_idx = base_idx - shift

            if col_idx < 0:
                # Shifted off the left edge of the table — no attack.
                tally[no_effect] += 1
                off_table_count += 1
            else:
                row_key = _dr_row_key(final_dr, dr_rows)
                tally[results[row_key][col_idx]] += 1
                col_value = columns[col_idx]
                cell_counts[(col_value, row_key)] += 1
                active_columns.add(col_value)

    distribution = [
        {"result": r, "probability": round(c / 36, 4), "count": c}
        for r, c in tally.items()
    ]
    # Sort by probability desc, then result string for stable ordering.
    distribution.sort(key=lambda x: (-x["count"], x["result"]))

    # Cell grid: {column_value(str): {dr_row_key: {prob, count}}}
    by_column: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (col_value, row_key), c in cell_counts.items():
        by_column.setdefault(str(col_value), {})[row_key] = {
            "prob": round(c / 36, 4),
            "count": c,
        }

    return {
        "column": column,
        "drm": drm,
        "cowering": cowering,
        "distribution": distribution,
        "cowering_outcomes": cowering_count,
        "cells": {
            "by_column": by_column,
            "active_columns": sorted(active_columns),
            "off_table": {"prob": round(off_table_count / 36, 4), "count": off_table_count},
        },
        "sniper": _sniper_probabilities(san) if san is not None else None,
    }


def get_table() -> Dict[str, Any]:
    """The static IFT grid the UI renders the heatmap onto."""
    t = _load_table()
    return {
        "columns": t["columns"],
        "dr_rows": t["dr_rows"],
        "results": t["results"],
        "no_effect": t.get("no_effect", "—"),
    }


# =============================================================================
# Attack builder — compute_attack() and helpers
# =============================================================================

# A7.21: Point Blank Fire doubles FP; Triple Point Blank Fire (same Location)
# triples it. Small-arms / MG / ATR / IFE only — the caller is responsible for
# not flagging PBF on weapons it doesn't apply to.
PBF_MULTIPLIER = {"none": 1, "pbf": 2, "tpbf": 3}

# Ways to roll each 2d6 sum (out of 36) — for the second-roll MC convolution.
_SUM_WAYS = {s: sum(1 for d1 in range(1, 7) for d2 in range(1, 7) if d1 + d2 == s)
             for s in range(2, 13)}


def _fnum(x: Fraction) -> int | float:
    """Render an exact Fraction as an int when whole, else a float (e.g. 2.5)."""
    return int(x) if x.denominator == 1 else float(x)


def _resolve_unit_fp(
    unit: Dict[str, Any],
    afph: bool,
    opportunity_fire: bool,
    area_fire_halvings: int,
    warnings: List[str],
) -> Dict[str, Any]:
    """
    Adjusted FP for one firing unit with a human-readable audit trail.

    Per A7.2 all modifiers are cumulative and fractions are RETAINED per unit
    (only the attack total is mapped to a column), so we use exact Fractions.
    Assault fire is the one exception to "retain fractions": its +1 is added
    after all other modification and the unit's FP is then rounded UP (A7.36,
    A7.31 EX).
    """
    fp = Fraction(str(unit["fp"]))
    if fp <= 0:
        raise ValueError(f"Unit FP must be positive, got {unit['fp']!r}.")
    pbf = unit.get("pbf", "none")
    if pbf not in PBF_MULTIPLIER:
        raise ValueError(f"Invalid pbf {pbf!r}. Must be one of {list(PBF_MULTIPLIER)}.")

    val = fp
    steps: List[str] = []

    # A7.21: PBF ×2 / TPBF ×3.
    if pbf != "none":
        val *= PBF_MULTIPLIER[pbf]
        steps.append(f"×{PBF_MULTIPLIER[pbf]} {pbf.upper()} = {_fnum(val)}")
    # A7.22: long range halves FP.
    if unit.get("long_range"):
        val /= 2
        nr = unit.get("normal_range")
        rg = unit.get("_range_to_target")
        if nr is not None and rg is not None:
            steps.append(f"÷2 long range (range {rg} > normal range {nr}) = {_fnum(val)}")
        else:
            steps.append(f"÷2 long range = {_fnum(val)}")
    # A7.24: Advancing Fire halves FP — unless Opportunity Fire (A7.25).
    if afph and not opportunity_fire:
        val /= 2
        steps.append(f"÷2 AFPh = {_fnum(val)}")
    # A7.23 / A9.5 etc.: each area-fire condition (concealed target, spraying
    # fire, ...) halves again. Attack-wide: applies to every unit.
    for _ in range(area_fire_halvings):
        val /= 2
        steps.append(f"÷2 area fire = {_fnum(val)}")
    # A7.8: a pinned firer's FP is halved.
    if unit.get("pinned"):
        val /= 2
        steps.append(f"÷2 pinned = {_fnum(val)}")

    # A7.36: assault fire adds +1 after all other modification, then the
    # unit's FP is rounded up. NA at long range or during opportunity fire.
    if unit.get("assault_fire"):
        if unit.get("long_range") or opportunity_fire:
            why = "long range" if unit.get("long_range") else "opportunity fire"
            steps.append(f"assault fire NA ({why})")
            warnings.append(
                f"Assault fire +1 dropped for the {unit['fp']} FP unit: NA at {why} (A7.36)."
            )
        else:
            had_fraction = val.denominator != 1
            val = Fraction(math.ceil(val + 1))
            suffix = " (FRU)" if had_fraction else ""
            steps.append(f"+1 assault fire → {_fnum(val)}{suffix}")

    return {"fp": _fnum(Fraction(str(unit["fp"]))), "steps": steps, "final": _fnum(val),
            "_exact": val}


def _build_drm_ledger(
    tem: int,
    hindrance: int,
    ffnam: bool,
    ffmo: bool,
    leadership: int,
    encircled_firer: bool,
    other_drm: Optional[List[Dict[str, Any]]],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    """
    Itemized DRM ledger (A7.3, A7.6). Only contributing items are listed, so
    the answer can show its work line by line.

    FFMO is validated rather than trusted: A4.6 negates it when the shot has
    any LOS hindrance or the target Location has positive TEM. A dropped FFMO
    becomes a warning instead of silently changing the math.
    """
    ledger: List[Dict[str, Any]] = []
    if tem:
        ledger.append({"label": "TEM", "drm": tem})
    if hindrance:
        ledger.append({"label": "hindrance", "drm": hindrance})
    if ffnam:
        # A4.6: First Fire vs non-assault movement.
        ledger.append({"label": "FFNAM", "drm": -1})
    if ffmo:
        if hindrance > 0 or tem > 0:
            warnings.append(
                "FFMO dropped: it is negated by any LOS hindrance or positive "
                "TEM in the target Location (A4.6)."
            )
        else:
            ledger.append({"label": "FFMO", "drm": -1})
    if leadership:
        # A7.531: leader direction modifies the attack by his leadership DRM.
        ledger.append({"label": "leadership", "drm": leadership})
    if encircled_firer:
        # A7.7: an encircled unit attacks with a +1 DRM.
        ledger.append({"label": "encircled firer", "drm": 1})
    for item in other_drm or []:
        ledger.append({"label": str(item.get("label", "other")), "drm": int(item["drm"])})
    return ledger


def _derive_cowering(leadership: int, firer_cowering_exempt: bool, inexperienced: bool) -> str:
    """
    Cowering mode per A7.9: leader direction prevents cowering entirely, as do
    the listed exemptions (SMC, berserk/fanatic, certain nationalities,
    vehicular/IFE fire, fire lanes — caller flags these via
    `firer_cowering_exempt`). Inexperienced firers (A19.33) cower an extra
    column ("double"); everyone else cowers one ("regular").
    """
    if leadership != 0 or firer_cowering_exempt:
        return "none"
    if inexperienced:
        return "double"
    return "regular"


def _mc_probs(extra_drm: int, morale: int) -> Dict[str, Fraction]:
    """
    Second-roll 2d6 morale/task check vs `morale`, final DR = 2d6 + extra_drm.

    Returns exact probabilities for: fail (final DR > morale), exact pass
    (final DR == morale — the "pass by the skin of your teeth" case that pins
    per A7.8), and clean pass (final DR < morale).
    """
    fail = exact = clean = 0
    for total, ways in _SUM_WAYS.items():
        final = total + extra_drm
        if final > morale:
            fail += ways
        elif final == morale:
            exact += ways
        else:
            clean += ways
    return {
        "fail": Fraction(fail, 36),
        "exact": Fraction(exact, 36),
        "clean": Fraction(clean, 36),
    }


# Categories for the personnel-target convolution. The four headline keys are
# mutually exclusive and sum to 1; survivor_* track the K/# survivor's MC and
# overlap with eliminated_or_reduced (the reduction already happened).
_PERSONNEL_KEYS = ("eliminated_or_reduced", "broken", "pinned", "no_effect",
                   "survivor_broken", "survivor_pinned")


def _personnel_outcome(result: str, morale: int, mc_drm: int,
                       no_effect: str = "—") -> Dict[str, Fraction]:
    """
    Conditional outcome distribution for one IFT result string vs a personnel
    target of the given (effective) morale (A7.301–.306, A7.8).

      #KIA  → eliminated outright (A7.301).
      K/#   → casualty reduction, AND the survivor takes a #MC (A7.302).
      #MC   → break on final MC DR > morale; pin on final DR == morale
              (passed, but by the maximum — A7.8); otherwise unaffected.
      NMC   → as #MC with +0.
      PTC   → pinned on a failed task check (final DR > morale, A7.305);
              an exact pass of a PTC is a pass — no pin.
      —     → no effect.
    """
    out = {k: Fraction(0) for k in _PERSONNEL_KEYS}
    if result == no_effect:
        out["no_effect"] = Fraction(1)
        return out
    if re.fullmatch(r"\d+KIA", result):
        out["eliminated_or_reduced"] = Fraction(1)
        return out
    m = re.fullmatch(r"K/(\d+)", result)
    if m:
        # The reduction is certain; the survivor's #MC rides along as extra info.
        out["eliminated_or_reduced"] = Fraction(1)
        mc = _mc_probs(int(m.group(1)) + mc_drm, morale)
        out["survivor_broken"] = mc["fail"]
        out["survivor_pinned"] = mc["exact"]
        return out
    m = re.fullmatch(r"(\d+)MC", result) or re.fullmatch(r"(N)MC", result)
    if m:
        penalty = 0 if m.group(1) == "N" else int(m.group(1))
        mc = _mc_probs(penalty + mc_drm, morale)
        out["broken"] = mc["fail"]
        out["pinned"] = mc["exact"]
        out["no_effect"] = mc["clean"]
        return out
    if result == "PTC":
        mc = _mc_probs(mc_drm, morale)
        out["pinned"] = mc["fail"]
        out["no_effect"] = mc["exact"] + mc["clean"]
        return out
    raise ValueError(f"Unrecognized IFT result {result!r}.")


def _personnel_effects(dist: Dict[str, Any], target: Dict[str, Any],
                       no_effect: str) -> Dict[str, Any]:
    """Convolve the (post-cowering) IFT distribution with the target's MC dice."""
    morale = target.get("morale")
    if morale is None:
        raise ValueError("target.morale is required for a personnel target.")
    mc_drm = int(target.get("mc_drm", 0))
    encircled = bool(target.get("encircled", False))
    # A7.7: an encircled unit's morale is lowered by one.
    eff_morale = int(morale) - (1 if encircled else 0)

    totals = {k: Fraction(0) for k in _PERSONNEL_KEYS}
    for entry in dist["distribution"]:
        weight = Fraction(entry["count"], 36)
        branch = _personnel_outcome(entry["result"], eff_morale, mc_drm, no_effect)
        for k in _PERSONNEL_KEYS:
            totals[k] += weight * branch[k]

    return {
        "kind": "personnel",
        "morale": int(morale),
        "effective_morale": eff_morale,
        "mc_drm": mc_drm,
        "encircled": encircled,
        "p_eliminated_or_reduced": round(float(totals["eliminated_or_reduced"]), 4),
        "p_broken": round(float(totals["broken"]), 4),
        "p_pinned": round(float(totals["pinned"]), 4),
        "p_no_effect": round(float(totals["no_effect"]), 4),
        # K/# survivor's #MC — overlaps with eliminated_or_reduced by design.
        "survivor_mc": {
            "p_broken": round(float(totals["survivor_broken"]), 4),
            "p_pinned": round(float(totals["survivor_pinned"]), 4),
        },
        "note": (
            "The four p_* categories are mutually exclusive and sum to 1. "
            "survivor_mc is the K/# survivor's morale check and overlaps with "
            "p_eliminated_or_reduced."
        ),
    }


def _vehicle_effects(dist: Dict[str, Any], table: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unarmored-vehicle outcome per the IFT ★ vehicle line (A7.308): the SAME
    attack DR is compared to the column's kill number. Burning wreck on final
    DR ≤ half the kill# (FRD), eliminated on < kill#, immobilized on == kill#.

    The kill number depends on which column the DR was read on, so cowering
    matters — we use the per-cell (column, final-DR) counts rather than the
    result strings, which don't carry the column.
    """
    columns: List[int] = table["columns"]
    vehicle_cols: List[int] = table["vehicle_columns"]

    counts = {"burning_wreck": 0, "eliminated": 0, "immobilized": 0, "no_effect": 0}
    kill_numbers: Dict[str, int] = {}
    for col_str, rows in dist["cells"]["by_column"].items():
        kill = vehicle_cols[columns.index(int(col_str))]
        kill_numbers[col_str] = kill
        for row_key, cell in rows.items():
            # Row keys are clamped ('0' = ≤0, '15' = ≥15) but the comparisons
            # are unaffected: kill numbers run 3–13, so a ≤0 DR is always a
            # burning wreck and a ≥15 DR is always a miss.
            dr = int(row_key)
            if dr <= kill // 2:          # ≤ half kill#, FRD
                counts["burning_wreck"] += cell["count"]
            elif dr < kill:
                counts["eliminated"] += cell["count"]
            elif dr == kill:
                counts["immobilized"] += cell["count"]
            else:
                counts["no_effect"] += cell["count"]
    # Cowered off the table's left edge → no attack vs the vehicle either.
    counts["no_effect"] += dist["cells"]["off_table"]["count"]

    return {
        "kind": "vehicle",
        "kill_numbers": kill_numbers,
        "p_burning_wreck": round(counts["burning_wreck"] / 36, 4),
        "p_eliminated": round(counts["eliminated"] / 36, 4),
        "p_immobilized": round(counts["immobilized"] / 36, 4),
        "p_no_effect": round(counts["no_effect"] / 36, 4),
        "note": (
            "Categories are mutually exclusive and sum to 1; p_eliminated "
            "excludes burning wrecks, so total destroyed = p_burning_wreck + "
            "p_eliminated."
        ),
    }


def compute_attack(
    units: List[Dict[str, Any]],
    afph: bool = False,
    opportunity_fire: bool = False,
    area_fire_halvings: int = 0,
    tem: int = 0,
    hindrance: int = 0,
    ffnam: bool = False,
    ffmo: bool = False,
    leadership: int = 0,
    encircled_firer: bool = False,
    other_drm: Optional[List[Dict[str, Any]]] = None,
    inexperienced: bool = False,
    firer_cowering_exempt: bool = False,
    san: Optional[int] = None,
    target: Optional[Dict[str, Any]] = None,
    range_to_target: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build and resolve a full IFT attack from the situation. Pure and
    deterministic — pipeline per docs/ift_attack_tool_plan.md:

        units → adjusted FP (A7.2–.36) → column (A7.3)
              → DRM ledger (A7.6, A4.6, A7.531, A7.7)
              → cowering (A7.9) → distribution (compute_distribution)
              → optional target effects (A7.301–.308, A7.8)

    Args:
        units: Firing units, each {"fp": number, "pbf": "none"|"pbf"|"tpbf",
               "long_range": bool, "pinned": bool, "assault_fire": bool,
               "normal_range": int}. A squad firing a SW it mans is two entries.
               When range_to_target and a unit's normal_range are both given,
               long_range is DERIVED (range_to_target > normal_range) and any
               hand-set long_range flag is overridden.
        range_to_target: Range in hexes to the target. Lets the tool derive
               Long Range Fire (A7.22) per unit instead of trusting a flag.
        afph: Advancing Fire Phase — every unit's FP halved (A7.24) unless
              opportunity_fire (A7.25).
        opportunity_fire: Negates the AFPh halving; also makes assault fire NA.
        area_fire_halvings: Attack-wide count of area-fire halvings (concealed
              target, spraying fire, ...) — each halves every unit again.
        tem / hindrance / ffnam / ffmo / leadership / encircled_firer /
        other_drm: The DRM ledger; see _build_drm_ledger. FFMO is dropped with
              a warning when hindrance > 0 or tem > 0 (A4.6).
        inexperienced / firer_cowering_exempt: Cowering derivation inputs
              (A7.9); leadership != 0 also suppresses cowering.
        san: Enemy Sniper Activation Number (2–12) or None, as in
              compute_distribution.
        target: Optional {"kind": "personnel"|"vehicle", "morale": int,
              "mc_drm": int, "encircled": bool}. morale/mc_drm/encircled are
              personnel-only; the vehicle variant uses the IFT ★ kill numbers.

    Returns the compute_distribution payload augmented with `fp_breakdown`
    (per-unit audit trail), `total_fp`, `drm_breakdown`, `warnings`, and
    `vs_target` (None without a target). When total FP < 1 there is no valid
    attack: the dict carries an `error` plus the breakdowns, `column` is None,
    and no distribution is included.
    """
    table = _load_table()
    columns: List[int] = table["columns"]
    no_effect: str = table.get("no_effect", "—")

    if not units:
        raise ValueError("At least one firing unit is required.")
    if area_fire_halvings < 0:
        raise ValueError(f"area_fire_halvings must be >= 0, got {area_fire_halvings!r}.")
    if target is not None and target.get("kind", "personnel") not in ("personnel", "vehicle"):
        raise ValueError(f"Invalid target kind {target.get('kind')!r}.")

    warnings: List[str] = []

    # Derive Long Range Fire deterministically when the geometry is supplied
    # (A7.22): a unit fires at long range when range_to_target exceeds its
    # Normal Range. Preferred over a hand-set "long_range" flag — the model is
    # error-prone at this comparison, so let the tool own the arithmetic.
    if range_to_target is not None:
        if range_to_target < 0:
            raise ValueError(f"range_to_target must be >= 0, got {range_to_target!r}.")
        for u in units:
            nr = u.get("normal_range")
            if nr is None:
                warnings.append(
                    f"Cannot derive long range for the {u.get('fp')} FP unit: no "
                    "normal_range supplied; using the long_range flag as given (A7.22)."
                )
                continue
            derived = range_to_target > nr
            if u.get("long_range") is not None and bool(u.get("long_range")) != derived:
                warnings.append(
                    f"long_range flag ({u.get('long_range')}) for the {u.get('fp')} FP "
                    f"unit overridden: range {range_to_target} vs normal range {nr} "
                    f"→ {derived} (A7.22)."
                )
            u["long_range"] = derived
            u["_range_to_target"] = range_to_target  # audit-trail context only

    # ---- Layer 1: per-unit firepower, summed exactly, mapped to a column ----
    fp_breakdown = [
        _resolve_unit_fp(u, afph, opportunity_fire, area_fire_halvings, warnings)
        for u in units
    ]
    total = sum((u["_exact"] for u in fp_breakdown), Fraction(0))
    for u in fp_breakdown:           # internal exact value; not part of the API
        del u["_exact"]

    # ---- Layer 2: DRM ledger ----
    drm_breakdown = _build_drm_ledger(
        tem, hindrance, ffnam, ffmo, leadership, encircled_firer, other_drm, warnings
    )
    drm = sum(item["drm"] for item in drm_breakdown)

    # ---- Layer 3: cowering ----
    cowering = _derive_cowering(leadership, firer_cowering_exempt, inexperienced)

    base = {
        "fp_breakdown": fp_breakdown,
        "total_fp": _fnum(total),
        "drm_breakdown": drm_breakdown,
        "warnings": warnings,
    }

    # A7.3: the column is the rightmost one whose FP ≤ the attack total; a
    # total below the 1 FP column is no attack at all.
    if total < columns[0]:
        return {
            **base,
            "column": None,
            "drm": drm,
            "cowering": cowering,
            "vs_target": None,
            "error": (
                f"Total FP {_fnum(total)} is below the {columns[0]} FP column — "
                "no valid IFT attack."
            ),
        }
    column = max(c for c in columns if c <= total)

    dist = compute_distribution(column=column, drm=drm, cowering=cowering, san=san)

    # ---- Layer 4: target effects, convolved against the post-cowering
    # distribution (cowering already shifted columns inside the 36 cells). ----
    vs_target = None
    if target is not None:
        if target.get("kind", "personnel") == "vehicle":
            vs_target = _vehicle_effects(dist, table)
        else:
            vs_target = _personnel_effects(dist, target, no_effect)

    return {**base, **dist, "vs_target": vs_target}
