"""
To Hit / To Kill (TH/TK) probability engine — deterministic, no LLM.

Ordnance fire, ASL Chapter C. `compute_shot` resolves one ordnance shot as two
dependent rolls and returns exact probabilities (all 36 ordered TH dice, then
the effects roll per hit branch):

  1) To Hit — Basic TH# (C3 table, black/red by nationality) + C4 Gun & Ammo
     modifications = Modified TH#; Final TH DR = Original DR + TH DRM. Each of
     the 36 (colored, white) dice combos becomes a miss or a hit with:
       * Critical Hit (C3.7): Original 2 on the Vehicle/Area Target Type;
         Final DR < half the Modified TH# on the Infantry Target Type, or an
         Original 2 plus a subsequent dr of 1 / <= half the Modified TH#.
       * Improbable Hit (C3.6) and the "only the lowest DR hits" case (C3.7
         EXC) branch on a subsequent dr.
       * Location (C3.9): turret if colored dr < white dr, else hull; a hull
         hit vs a Hull Down target is a miss; a CH vs HD strikes the turret.
       * Multiple Hits (C3.8): 15-40mm Gun, doubles, non-CH, Vehicle/Infantry
         Target Type -> two effects DR, firer keeps the better one.
       * Malfunction (A9.7: Original DR >= B#, shot still resolved) and ROF
         retention (C2.24: colored dr <= ROF) are reported alongside.
  2) Effects —
       * Armored vehicle: Basic TK# (C7.31-.34 by ammo), x2 on a CH (C7.23),
         +1 vs rear Target Facing (C7.21), + Case D range (AP/APCR/APDS only,
         C7.24), minus the struck Aspect's AF (C7.11) = Final TK#; read
         against C7.7 Direct Fire (Burn / Elim / Immob-or-Shock / HE Shock /
         Possible Shock); Original TK DR 12 is a Dud (C7.35).
       * Unarmored vehicle: the table's unarmored Final TK# (doubled on a CH),
         read against the Unarmored Vehicle Destruction Table.
       * Personnel (Infantry/Area Target Type): IFT attack with the gun's HE FP
         (or HE Equivalency, C8.31), halved for the Area Target Type (C3.33);
         a CH doubles the full FP and reverses positive TEM (C3.71). Uses the
         same IFT grid as app.asl.ift.

Not modelled (reported as notes): Area Target Type hits on vehicles (C1.55),
Deliberate Immobilization, Intensive Fire, ammo depletion, Red CS# burn DRM,
the Possible Shock NTC, and Vehicle-Note special TK numbers (pass basic_tk).

All table data lives in `thtk_tables.json`.
"""

import json
import math
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.asl import ift

_TABLE_PATH = Path(__file__).with_name("thtk_tables.json")
_TABLE: Optional[Dict[str, Any]] = None

_DICE: List[Tuple[int, int]] = [(c, w) for c in range(1, 7) for w in range(1, 7)]
_P36 = Fraction(1, 36)
_P6 = Fraction(1, 6)

# Effects ranked most-severe first; used to pick the better of a Multiple Hit's
# two DR. Shock outranks Immobilized (it silences the AFV now); that ordering is
# an assumption, and the firer may choose otherwise.
VEHICLE_RESULTS = ["Burn", "Elim", "Shock", "Immobilized", "Possible Shock", "No effect"]


def _load() -> Dict[str, Any]:
    global _TABLE
    if _TABLE is None:
        with open(_TABLE_PATH, "r", encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def _band(rng: int, bands: Sequence[int]) -> int:
    for i, top in enumerate(bands):
        if rng <= top:
            return i
    return len(bands) - 1


def _beyond_12(rng: int) -> int:
    """Number of 12-hex increments (or fractions) beyond 12 hexes (C4.2/C4.3)."""
    return 0 if rng <= 12 else math.ceil((rng - 12) / 12)


def _bucket(mm: int, min_mm: Sequence[int]) -> Optional[int]:
    """Index of the highest bucket whose lower bound is <= mm, or None."""
    idx = None
    for i, lo in enumerate(min_mm):
        if mm >= lo:
            idx = i
    return idx


def _lookup(table: Dict[str, Any], key: str, nationality: str) -> Optional[int]:
    v = table.get(key)
    if isinstance(v, dict):
        return v.get(nationality, v.get("default"))
    return v


def parse_gun(gun: str) -> Tuple[int, str]:
    """'75L' -> (75, 'L'); '37*' -> (37, '*'); '88' -> (88, '')."""
    m = re.fullmatch(r"\s*(\d+)\s*(\*|LL|L)?\s*(?:mm)?\s*", gun or "", re.I)
    if not m:
        raise ValueError(f"Can't read gun size {gun!r}; expected e.g. '75L', '37*', '88LL'.")
    return int(m.group(1)), (m.group(2) or "").upper()


def th_color_for(nationality: str, year: Optional[int] = None) -> Tuple[str, Optional[str]]:
    """Ordnance TH# color from the National Capabilities Chart -> (color, note)."""
    entry = _load()["th_color"].get(nationality)
    if entry is None:
        return "black", f"No TH# color on file for {nationality or 'an unnamed nationality'}; assumed black."
    if isinstance(entry, dict):  # American
        if year is None:
            return "black", "American ordnance uses red TH# before 1944; assumed 1944+ (black)."
        return (entry["from_1944"] if year >= 1944 else entry["before_1944"]), None
    return entry, None


# ---------------------------------------------------------------------------
# To Hit
# ---------------------------------------------------------------------------

def modified_to_hit(target_type: str, rng: int, mm: int, barrel: str, ammo: str,
                    color: str, nationality: str = "") -> Dict[str, Any]:
    """Basic TH# (C3) + C4 Gun & Ammo modifications -> Modified TH# with a ledger."""
    t = _load()["to_hit"]
    bi = _band(rng, t["range_bands"])
    basic = t["basic"][target_type][color][bi]
    mods: List[Dict[str, Any]] = []
    if rng > 12:
        if barrel == "*":
            mods.append({"label": "* barrel", "mod": -1, "rule": "C4.11"})
        elif barrel == "L":
            mods.append({"label": "L barrel", "mod": 1, "rule": "C4.12"})
        elif barrel == "LL":
            mods.append({"label": "LL barrel", "mod": 2 if rng > 24 else 1, "rule": "C4.13"})
        if mm <= 57:
            mods.append({"label": "≤57mm gun", "mod": -_beyond_12(rng), "rule": "C4.2"})
        if mm <= 40:
            mods.append({"label": "≤40mm gun", "mod": -1, "rule": "C4.2"})
        apcr_always = nationality == "German" and (mm, barrel) in {(28, "LL"), (40, "LL")}
        if ammo in ("APCR", "APDS") or apcr_always:
            mods.append({"label": f"{ammo if ammo in ('APCR', 'APDS') else 'APCR (28LL/40LL)'}",
                         "mod": -_beyond_12(rng), "rule": "C4.3"})
    elif ammo == "SMOKE":
        mods.append({"label": "SMOKE ≤12 hexes", "mod": 2, "rule": "C4.4"})
    return {
        "basic": basic,
        "color": color,
        "range_band": t["range_labels"][bi],
        "modifications": mods,
        "modified": basic + sum(m["mod"] for m in mods),
    }


def _th_events(target_type: str, th: int, drm: int, hull_down: bool,
               multiple_ok: bool) -> List[Dict[str, Any]]:
    """Every distinct TH outcome with its probability.

    Each event: {p, hit, ch, loc ('hull'|'turret'|None), multiple, original,
    colored, white}. Subsequent-dr branches split one dice combo into several
    events.
    """
    improbable = 2 + drm > th          # C3.6
    only_lowest = 2 + drm == th        # C3.7 EXC: only an Original 2 hits
    events: List[Dict[str, Any]] = []

    def add(p, c, w, hit, ch=False, loc=None, multiple=False):
        events.append({"p": p, "hit": hit, "ch": ch, "loc": loc,
                       "multiple": multiple, "original": c + w, "colored": c, "white": w})

    def dice_loc(c, w):
        return "turret" if c < w else "hull"

    for c, w in _DICE:
        orig = c + w
        final = orig + drm
        if improbable:
            if orig != 2:
                add(_P36, c, w, False)
                continue
            # Subsequent dr: 1 CH, 2 turret, 3 hull (miss if HD), 4-6 miss.
            add(_P36 * _P6, c, w, True, ch=True, loc="turret" if hull_down else "hull")
            add(_P36 * _P6, c, w, True, loc="turret")
            add(_P36 * _P6, c, w, not hull_down, loc=None if hull_down else "hull")
            add(_P36 * 3 * _P6, c, w, False)
            continue
        if final > th:
            add(_P36, c, w, False)
            continue
        doubles = c == w
        if target_type == "infantry":
            if 2 * final < th:
                add(_P36, c, w, True, ch=True)
            elif orig == 2:
                n_ch = sum(1 for d in range(1, 7) if d == 1 or 2 * d <= th)
                if n_ch:
                    add(_P36 * n_ch * _P6, c, w, True, ch=True)
                if n_ch < 6:
                    add(_P36 * (6 - n_ch) * _P6, c, w, True, multiple=multiple_ok)
            else:
                add(_P36, c, w, True, multiple=multiple_ok and doubles)
            continue
        # Vehicle / Area Target Type.
        if orig == 2 and only_lowest:
            add(_P36 * _P6, c, w, True, ch=True, loc="turret" if hull_down else "hull")
            loc = "turret" if hull_down else "hull"
            add(_P36 * 5 * _P6, c, w, True, loc=loc,
                multiple=multiple_ok and target_type == "vehicle")
            continue
        if orig == 2:
            add(_P36, c, w, True, ch=True, loc="turret" if hull_down else "hull")
            continue
        loc = dice_loc(c, w)
        if hull_down and loc == "hull":
            add(_P36, c, w, False, loc="hull")  # C3.9: a hull hit vs HD misses
            continue
        add(_P36, c, w, True, loc=loc,
            multiple=multiple_ok and doubles and target_type == "vehicle")
    return events


# ---------------------------------------------------------------------------
# To Kill
# ---------------------------------------------------------------------------

def _ap_case_d(mm: int, rng: int) -> Optional[int]:
    cd = _load()["ap"]["case_d"]
    row = "le25" if mm < 37 else ("37_57" if mm < 65 else "ge65")
    return cd[row][_band(rng, cd["range_bands"])]


def _apcr_case_d(mm: int, rng: int, ammo: str) -> Optional[int]:
    cd = _load()["apcr_apds"]["case_d"]
    row = "apds" if ammo == "APDS" else ("apcr_le57" if mm <= 57 else "apcr_ge75")
    return cd[row][_band(rng, cd["range_bands"])]


def basic_to_kill(mm: int, barrel: str, ammo: str, nationality: str = "") -> Dict[str, Any]:
    """Basic TK# vs an armored target and the table it came from. Raises
    ValueError when the gun isn't on that table (pass basic_tk instead)."""
    t = _load()
    key = f"{mm}{barrel}"
    if ammo == "AP":
        v = _lookup(t["ap"]["basic_tk"], key, nationality)
        table, rule = "AP", "C7.31"
    elif ammo in ("APCR", "APDS"):
        akey = f"D{key}" if ammo == "APDS" else key
        v = _lookup(t["apcr_apds"]["basic_tk"], akey, nationality)
        table, rule = "APCR/APDS", "C7.32"
    elif ammo == "HEAT":
        v = t["heat"]["basic_tk_by_mm"].get(str(mm))
        table, rule = "HEAT", "C7.33"
    elif ammo == "HE":
        i = _bucket(mm, t["he"]["min_mm"])
        v = None if i is None else t["he"]["armored_basic_tk"][i]
        table, rule = "HE", "C7.34"
    else:
        raise ValueError(f"{ammo} has no To Kill number.")
    if v is None:
        raise ValueError(
            f"{key} {ammo} is not on the {table} To Kill Table"
            f"{' for ' + nationality if nationality else ''} ({rule}); "
            "pass basic_tk from the counter / Vehicle Note.")
    return {"basic": v, "table": table, "rule": rule}


def unarmored_final_tk(mm: int, ammo: str) -> int:
    t = _load()
    if ammo == "HEAT":
        return t["heat"]["unarmored_final_tk"]
    if ammo == "HE":
        i = _bucket(mm, t["he"]["min_mm"])
        if i is None:
            raise ValueError(f"{mm}mm HE is below the HE To Kill Table (20mm+).")
        return t["he"]["unarmored_final_tk"][i]
    u = t["ap"]["unarmored_final_tk"]
    return u["tk"][_bucket(mm, u["min_mm"])]


def _tk_result(final_dr: int, original: int, ftk: int, loc: str, he: bool,
               armored: bool) -> str:
    """C7.7 AFV / Unarmored Vehicle Destruction Table, Direct Fire column."""
    if original == 12:
        return "No effect"  # Dud, C7.35
    if 2 * final_dr <= ftk:
        return "Burn"
    if final_dr < ftk:
        return "Elim"
    if final_dr == ftk:
        if not armored:
            return "Immobilized"
        return "Immobilized" if loc == "hull" else "Shock"
    if armored and final_dr == ftk + 1:
        if he:
            return "Immobilized" if loc == "hull" else "Shock"
        return "Possible Shock"
    return "No effect"


def _tk_dist(ftk: int, loc: str, he: bool, armored: bool) -> Dict[str, Fraction]:
    out: Dict[str, Fraction] = defaultdict(Fraction)
    for c, w in _DICE:
        out[_tk_result(c + w, c + w, ftk, loc, he, armored)] += _P36
    return out


def _tk_multiple(ftk_by_loc: Dict[str, int], loc1: str, he: bool,
                 armored: bool) -> Dict[str, Fraction]:
    """Multiple Hit vs a vehicle (C3.8): two TK DR, firer keeps the better.
    The first TK DR's dice also locate the second hit (C3.9)."""
    rank = {r: i for i, r in enumerate(VEHICLE_RESULTS)}
    out: Dict[str, Fraction] = defaultdict(Fraction)
    for c1, w1 in _DICE:
        r1 = _tk_result(c1 + w1, c1 + w1, ftk_by_loc[loc1], loc1, he, armored)
        loc2 = "turret" if c1 < w1 else "hull"
        for c2, w2 in _DICE:
            r2 = _tk_result(c2 + w2, c2 + w2, ftk_by_loc[loc2], loc2, he, armored)
            out[min(r1, r2, key=rank.__getitem__)] += _P36 * _P36
    return out


# ---------------------------------------------------------------------------
# Personnel effects (IFT)
# ---------------------------------------------------------------------------

def he_fp(mm: int) -> Optional[int]:
    t = _load()["he_fp"]
    i = _bucket(mm, t["min_mm"])
    return None if i is None else t["fp"][i]


def _ift_column(fp: float) -> Optional[int]:
    cols = [c for c in ift.valid_columns() if c <= fp]
    return cols[-1] if cols else None


def _ift_rank(result: str) -> Tuple[int, int]:
    """Higher = more severe: nKIA > K/n > nMC > NMC > PTC > none."""
    if result.endswith("KIA"):
        return (5, int(result[:-3]))
    if result.startswith("K/"):
        return (4, int(result[2:]))
    if result.endswith("MC") and result[0].isdigit():
        return (3, int(result[0]))
    return {"NMC": (2, 0), "PTC": (1, 0)}.get(result, (0, 0))


def _ift_result(column: int, final_dr: int) -> str:
    table = ift._load_table()
    row = ift._dr_row_key(final_dr, table["dr_rows"])
    return table["results"][row][table["columns"].index(column)]


def _ift_dist(column: Optional[int], drm: int, multiple: bool) -> Dict[str, Fraction]:
    out: Dict[str, Fraction] = defaultdict(Fraction)
    if column is None:
        out["—"] += 1
        return out
    sums = [c + w for c, w in _DICE]
    if not multiple:
        for s in sums:
            out[_ift_result(column, s + drm)] += _P36
        return out
    for s1 in sums:
        r1 = _ift_result(column, s1 + drm)
        for s2 in sums:
            r2 = _ift_result(column, s2 + drm)
            out[max(r1, r2, key=_ift_rank)] += _P36 * _P36
    return out


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _fmt(d: Dict[str, Fraction], order: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    items = [(k, v) for k, v in d.items() if v]
    if order:
        items.sort(key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))
    else:
        items.sort(key=lambda kv: _ift_rank(kv[0]) if kv[0] not in ("Miss",) else (-1, 0),
                   reverse=True)
    return [{"result": k, "probability": round(float(v), 4)} for k, v in items]


def compute_shot(
    *,
    gun: Optional[str] = None,
    mm: Optional[int] = None,
    barrel: str = "",
    nationality: str = "",
    year: Optional[int] = None,
    th_color: Optional[str] = None,
    ammo: str = "AP",
    target_type: str = "vehicle",
    range_hexes: int,
    th_drm: Sequence[Dict[str, Any]] = (),
    tem: int = 0,
    target: Optional[Dict[str, Any]] = None,
    basic_tk: Optional[int] = None,
    b_number: int = 12,
    rof: int = 0,
    fp: Optional[float] = None,
    effects_drm: int = 0,
) -> Dict[str, Any]:
    """Resolve one ordnance shot. See the module docstring for the rules.

    gun / mm+barrel: firing weapon ('75L', or mm=75, barrel='L').
    th_color: 'black'/'red' override; default from nationality (and year).
    th_drm: To Hit DRM ledger [{"label", "drm"}] (C5/C6 cases), positive = harder.
    tem: target's positive TEM. TH DRM (Case Q) on the Vehicle/Infantry Target
        Type; Effects DRM on the Area Target Type (C3.331); reversed on a CH vs
        personnel (C3.71).
    target: {"kind": "armored", "facing": "front|side|rear", "hull_af",
             "turret_af", "hull_down"} | {"kind": "unarmored"} |
            {"kind": "personnel"} (default for Infantry/Area Target Types).
    basic_tk: override the table Basic TK# (Vehicle-Note specials).
    fp: override the IFT FP vs personnel (default: HE FP by caliber / C8.31).
    effects_drm: other IFT DRM vs personnel (FFMO/FFNAM, Air Burst...).
    """
    t = _load()
    if gun:
        mm, barrel = parse_gun(gun)
    if not mm or mm <= 0:
        raise ValueError("Give the gun size, e.g. gun='75L' or mm=75, barrel='L'.")
    barrel = (barrel or "").upper()
    if barrel not in t["barrels"]:
        raise ValueError(f"Invalid barrel {barrel!r}. One of {t['barrels']}.")
    ammo = (ammo or "").upper()
    if ammo not in t["ammo_types"]:
        raise ValueError(f"Invalid ammo {ammo!r}. One of {t['ammo_types']}.")
    if target_type not in t["target_types"]:
        raise ValueError(f"Invalid target_type {target_type!r}. One of {t['target_types']}.")
    if range_hexes is None or range_hexes < 0:
        raise ValueError("range_hexes must be >= 0.")

    target = dict(target or {})
    kind = target.get("kind") or ("armored" if target_type == "vehicle" else "personnel")
    notes: List[str] = []

    # --- legality (C3.31-.33) ---
    if ammo == "SMOKE" and target_type != "area":
        raise ValueError("SMOKE must use the Area Target Type (C3.33).")
    if target_type == "area" and ammo not in ("HE", "SMOKE"):
        raise ValueError("The Area Target Type is for HE or SMOKE only (C3.33).")
    if target_type == "area" and range_hexes == 0:
        raise ValueError("The Area Target Type can't be used in the firer's own hex (C3.33).")
    if target_type == "infantry" and kind == "armored":
        raise ValueError("The Infantry Target Type can't attack an AFV (C3.32/C3.41).")
    if target_type == "vehicle" and kind == "personnel":
        raise ValueError("The Vehicle Target Type fires only at a vehicle (C3.31).")
    if target_type == "infantry" and ammo != "HE" and kind == "personnel" and ammo != "SMOKE":
        notes.append("AP/HEAT on the Infantry Target Type is only allowed vs an unarmored "
                     "target (C3.32); HEAT vs Infantry also needs wall/building/rubble/"
                     "pillbox TEM (C8.31).")

    # --- To Hit ---
    if th_color:
        color = th_color.lower()
        if color not in ("black", "red"):
            raise ValueError("th_color must be 'black' or 'red'.")
    else:
        color, note = th_color_for(nationality, year)
        if note:
            notes.append(note)
    mth = modified_to_hit(target_type, range_hexes, mm, barrel, ammo, color, nationality)
    drm_lines = [{"label": str(d.get("label", "DRM")), "drm": int(d.get("drm", 0))}
                 for d in th_drm]
    if tem and target_type != "area":
        drm_lines.append({"label": "TEM (Case Q)", "drm": int(tem)})
    total_drm = sum(d["drm"] for d in drm_lines)

    hull_down = bool(target.get("hull_down")) and kind == "armored"
    multiple_ok = 15 <= mm <= 40 and target_type in ("vehicle", "infantry") and ammo != "SMOKE"
    events = _th_events(target_type, mth["modified"], total_drm, hull_down, multiple_ok)

    p_hit = sum(e["p"] for e in events if e["hit"])
    p_ch = sum(e["p"] for e in events if e["hit"] and e["ch"])
    p_mult = sum(e["p"] for e in events if e["hit"] and e["multiple"])
    to_hit = {
        **mth,
        "modified_th": mth["modified"],
        "drm": drm_lines,
        "total_drm": total_drm,
        "needed_original_dr": mth["modified"] - total_drm,
        "improbable": 2 + total_drm > mth["modified"],
        "p_hit": round(float(p_hit), 4),
        "p_critical": round(float(p_ch), 4),
        "p_multiple": round(float(p_mult), 4),
        "p_turret": round(float(sum(e["p"] for e in events if e["hit"] and e["loc"] == "turret")), 4),
        "p_hull": round(float(sum(e["p"] for e in events if e["hit"] and e["loc"] == "hull")), 4),
        "p_malfunction": round(sum(1 for c, w in _DICE if c + w >= b_number) / 36, 4),
        "p_rof_retained": round(sum(1 for c, w in _DICE if c <= rof) / 36, 4),
    }
    del to_hit["modified"]
    if to_hit["improbable"]:
        notes.append("Improbable Hit: only an Original 2 followed by a dr of 1-3 hits (C3.6).")

    result: Dict[str, Any] = {
        "gun": f"{mm}{barrel}", "mm": mm, "barrel": barrel, "nationality": nationality,
        "ammo": ammo, "target_type": target_type, "range": range_hexes,
        "target": {**target, "kind": kind}, "to_hit": to_hit, "notes": notes,
    }

    # --- Effects ---
    if ammo == "SMOKE":
        result["outcomes"] = [{"result": "SMOKE placed", "probability": to_hit["p_hit"]},
                              {"result": "Miss", "probability": round(1 - to_hit["p_hit"], 4)}]
        return result
    if target_type == "area" and kind != "personnel":
        notes.append("An Area Target Type hit vs a vehicle is resolved on the IFT (C1.55/"
                     "C3.332); not modelled here.")
        result["outcomes"] = [{"result": "Hit", "probability": to_hit["p_hit"]},
                              {"result": "Miss", "probability": round(1 - to_hit["p_hit"], 4)}]
        return result

    total: Dict[str, Fraction] = defaultdict(Fraction)
    total["Miss"] += 1 - p_hit

    if kind == "personnel":
        result["to_kill"] = _personnel_effects(mm, ammo, target_type, tem, fp,
                                               effects_drm, events, total, notes)
        result["outcomes"] = _fmt(total)
        return result

    armored = kind == "armored"
    he = ammo == "HE"
    if armored:
        for k in ("hull_af", "turret_af"):
            if target.get(k) is None:
                raise ValueError(f"Armored target needs {k} (AF of the facing being fired on).")
        facing = (target.get("facing") or "front").lower()
        if facing not in ("front", "side", "rear"):
            raise ValueError("target facing must be front, side or rear.")
        if basic_tk is not None:
            btk = {"basic": int(basic_tk), "table": ammo, "rule": "override"}
        else:
            btk = basic_to_kill(mm, barrel, ammo, nationality)
        case_d = None
        if ammo == "AP":
            case_d = _ap_case_d(mm, range_hexes)
        elif ammo in ("APCR", "APDS"):
            case_d = _apcr_case_d(mm, range_hexes, ammo)
        cannot_kill = ammo in ("AP", "APCR", "APDS") and case_d is None
        if cannot_kill:
            notes.append(f"Case D is NA at {range_hexes} hexes: this gun can't kill an AFV "
                         "at that range (C7.24).")

        def final_tk(ch: bool, loc: str) -> Dict[str, Any]:
            lines = [{"label": f"Basic TK# ({btk['table']})", "value": btk["basic"]}]
            v = btk["basic"]
            if ch:
                lines.append({"label": "Critical Hit ×2 (C7.23)", "value": v})
                v *= 2
            if facing == "rear":
                lines.append({"label": "Rear Target Facing (C7.21)", "value": 1})
                v += 1
            if case_d is not None and case_d != 0:
                lines.append({"label": "Case D range (C7.24)", "value": case_d})
                v += case_d
            af = int(target["turret_af" if loc == "turret" else "hull_af"])
            lines.append({"label": f"{loc} AF (C7.11)", "value": -af})
            return {"final": v - af, "lines": lines}

        branches = []
        for ch in (False, True):
            for loc in ("hull", "turret"):
                p = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch and e["loc"] == loc)
                if not p:
                    continue
                single = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch
                             and e["loc"] == loc and not e["multiple"])
                mult = p - single
                tk = final_tk(ch, loc)
                if cannot_kill:
                    dist = {"No effect": Fraction(1)}
                else:
                    dist = _tk_dist(tk["final"], loc, he, True)
                for r, q in dist.items():
                    total[r] += single * q
                if mult and cannot_kill:
                    total["No effect"] += mult
                elif mult:
                    other = "turret" if loc == "hull" else "hull"
                    ftk = {loc: tk["final"], other: final_tk(ch, other)["final"]}
                    for r, q in _tk_multiple(ftk, loc, he, True).items():
                        total[r] += mult * q
                branches.append({
                    "critical": ch, "location": loc, "probability": round(float(p), 4),
                    "multiple_hit_probability": round(float(mult), 4),
                    "final_tk": tk["final"], "tk_lines": tk["lines"],
                    "effects": _fmt(dist, VEHICLE_RESULTS),
                })
        result["to_kill"] = {"basic_tk": btk, "case_d": case_d, "branches": branches}
        notes.append("Possible Shock = the AFV takes a NTC; failure means Shock (C7.41).")
    else:
        base = unarmored_final_tk(mm, ammo)
        branches = []
        for ch in (False, True):
            p = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch)
            if not p:
                continue
            ftk = base * 2 if ch else base
            single = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch and not e["multiple"])
            dist = _tk_dist(ftk, "hull", he, False)
            for r, q in dist.items():
                total[r] += single * q
            if p - single:
                for r, q in _tk_multiple({"hull": ftk, "turret": ftk}, "hull", he, False).items():
                    total[r] += (p - single) * q
            branches.append({"critical": ch, "probability": round(float(p), 4),
                             "final_tk": ftk, "effects": _fmt(dist, VEHICLE_RESULTS)})
        result["to_kill"] = {"unarmored_final_tk": base, "branches": branches}

    result["outcomes"] = _fmt(total, VEHICLE_RESULTS + ["Miss"])
    return result


def _personnel_effects(mm, ammo, target_type, tem, fp_override, effects_drm,
                       events, total, notes) -> Dict[str, Any]:
    t = _load()
    if fp_override is not None:
        full = float(fp_override)
        source = "override"
    elif ammo == "HE":
        full = he_fp(mm)
        source = "HE FP by caliber (IFT)"
        if full is None:
            raise ValueError(f"{mm}mm has no HE FP on the IFT (20mm+).")
    elif ammo in ("AP", "APCR", "APDS"):
        eq = t["he_equivalency"]
        full = eq["ap_lt37"] if mm < 37 else eq["ap_ge37"]
        source = "AP HE Equivalency (C8.31)"
    else:  # HEAT: column left of the gun's normal HE FP
        cols = ift.valid_columns()
        normal = _ift_column(he_fp(mm) or 0)
        full = cols[cols.index(normal) - 1] if normal and cols.index(normal) > 0 else None
        source = "HEAT HE Equivalency (C8.31)"
        if full is None:
            raise ValueError("No HEAT HE Equivalency for this gun; pass fp.")

    normal_fp = full / 2 if target_type == "area" else full
    normal_drm = effects_drm + (tem if target_type == "area" else 0)
    ch_fp = full * 2
    ch_drm = effects_drm - max(tem, 0)
    if target_type == "area":
        notes.append("Area Target Type: half FP, TEM applies to the Effects DR (C3.33/C3.331).")

    branches = []
    for ch in (False, True):
        p = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch)
        if not p:
            continue
        fpv, drm = (ch_fp, ch_drm) if ch else (normal_fp, normal_drm)
        col = _ift_column(fpv)
        single = sum(e["p"] for e in events if e["hit"] and e["ch"] == ch and not e["multiple"])
        dist = _ift_dist(col, drm, False)
        for r, q in dist.items():
            total[r] += single * q
        if p - single:
            for r, q in _ift_dist(col, drm, True).items():
                total[r] += (p - single) * q
        branches.append({"critical": ch, "probability": round(float(p), 4),
                         "fp": fpv, "ift_column": col, "effects_drm": drm,
                         "effects": _fmt(dist)})
    return {"fp_source": source, "full_fp": full, "branches": branches}


def get_options() -> Dict[str, Any]:
    t = _load()
    return {
        "barrels": t["barrels"],
        "ammo_types": t["ammo_types"],
        "target_types": t["target_types"],
        "nationalities": [k for k in t["th_color"] if not k.startswith("_")],
        "facings": ["front", "side", "rear"],
    }
