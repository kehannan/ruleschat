"""
To Hit / To Kill (TH/TK) probability engine — deterministic, no LLM.

Ordnance combat, ASL Chapter C. Given a firing Gun (caliber, barrel class, ammo,
nationality) and a target (type, range, armor), `compute_flow` resolves the two
dependent 2d6 rolls as a flow tree:

  1) To Hit  — classify all 36 ordered dice combos vs the Final TH# (from the C3
     table) into Critical / Multiple / Normal hit branches (or Miss).
  2) To Kill — per branch, the conditional probability of each outcome (Burn/Elim,
     Immobilized, Shock, Possible Shock) from the gun's To Kill# (C7.31 BASIC TK# +
     Case D range modifier) minus the struck Aspect's armor, read against C7.7.

A To Hit dr of 2 always hits and 12 always misses. Facing is derived from the dice:
turret if the colored dr < the white dr, else hull (so doubles always strike hull).

Provisional / v1 simplifications (flagged in the UI):
  - Critical Hit is fixed to the original DR 2 (real CH triggers on doubles ≤ a
    gun-specific CH#); it doubles the gun's To Kill# and always strikes the hull.
  - Multiple Hit (two To Kill DRs, most-severe-of-two) is gated to 15–40mm guns vs
    Vehicle/Infantry per rule 3.8; for other guns a non-CH doubles hit is a Normal
    hull hit.
  - AP To Kill table only; APDS/APCR treated as AP, HEAT/HE not modeled. BASIC TK#
    numbers still being verified.

All table data lives in `thtk_tables.json`.
"""

import json
from pathlib import Path
from collections import Counter
from typing import Dict, Any, List, Optional

_TABLE_PATH = Path(__file__).with_name("thtk_tables.json")
_TABLE: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _TABLE
    if _TABLE is None:
        with open(_TABLE_PATH, "r", encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


# 2d6 sum -> number of the 36 ordered outcomes producing it.
_TWO_DICE: Counter = Counter(a + b for a in range(1, 7) for b in range(1, 7))


def _round(p: float) -> float:
    return round(p, 4)


def _bracket_index(rng: int, brackets: List[Dict[str, Any]]) -> int:
    for i, b in enumerate(brackets):
        if rng <= b["max"]:
            return i
    return len(brackets) - 1


_WEAPON_SUFFIX = {"Normal": "", "*": "*", "L": "L", "LL": "LL"}


def _gun_size_key(mm: int, weapon_type: str) -> str:
    """The To Kill gun-size string, e.g. (37, 'L') -> '37L', (75, 'Normal') -> '75'."""
    return f"{mm}{_WEAPON_SUFFIX.get(weapon_type, '')}"


def _case_d_bucket(tk: Dict[str, Any], mm: int) -> str:
    """Map mm to a Case D row key (le25 / mid / ge65)."""
    if mm <= tk["case_d"]["le25"]["max_mm"]:
        return "le25"
    if mm <= tk["case_d"]["mid"]["max_mm"]:
        return "mid"
    return "ge65"


def _validate(t, target_type, weapon_type, ammo, nationality, rng, mm):
    if target_type not in t["target_types"]:
        raise ValueError(f"Invalid target_type {target_type!r}. One of {t['target_types']}.")
    if weapon_type not in t["weapon_types"]:
        raise ValueError(f"Invalid weapon_type {weapon_type!r}. One of {t['weapon_types']}.")
    if ammo not in t["ammo_types"]:
        raise ValueError(f"Invalid ammo {ammo!r}. One of {t['ammo_types']}.")
    if nationality and nationality not in t["nationalities"]:
        raise ValueError(f"Invalid nationality {nationality!r}. One of {t['nationalities']}.")
    if rng < 0:
        raise ValueError(f"Invalid range {rng!r}. Must be >= 0.")
    if mm <= 0:
        raise ValueError(f"Invalid weapon size {mm!r}mm. Must be > 0.")


def final_to_hit(target_type, rng, weapon_type, ammo, mm, nationality="", th_drm=0):
    """
    Final To Hit number from the C3 TO HIT TABLE + C4 modifications.

    Basic TH# depends on target type, range band, and nationality (German optics use
    the higher column). C4 modifications add to the basic number (positive = easier):
    barrel class, ammo, and small-gun size mods (≤57mm and ≤40mm STACK). The Hit
    Determination DRM is a die modifier (positive = harder), subtracted. Returns
    (final_th, basic, modifiers).
    """
    t = _load()
    th = t["to_hit"]
    bi = _bracket_index(rng, t["range_brackets"])
    bracket = t["range_brackets"][bi]

    nat_class = "German" if nationality == th["german_nationality"] else "Other"
    basic = th["basic"][target_type][nat_class][bi]

    mods = th["mods"]
    modifiers = [{"label": f"{weapon_type} weapon", "drm": mods["weapon_type"][weapon_type][bi]}]
    ammo_val = mods["ammo"][ammo][bi]
    if ammo != "AP/HE" or ammo_val != 0:
        modifiers.append({"label": f"{ammo} ammo", "drm": ammo_val})
    for spec in (mods["size"]["le57"], mods["size"]["le40"]):
        if mm <= spec["max_mm"]:
            modifiers.append({"label": f"≤{spec['max_mm']}mm gun", "drm": spec["drm"][bi]})
    if th_drm:
        modifiers.append({"label": "Hit determination DRM", "drm": -th_drm})

    final_th = basic + sum(m["drm"] for m in modifiers)
    return final_th, basic, modifiers


def gun_to_kill(mm, rng, ammo, weapon_type, nationality=""):
    """
    The gun's To Kill# (C7.31 BASIC TK# + Case D range modifier), before armor.

    basic_tk is an int (all nationalities) or a per-country object with an optional
    "default"; the firer's nationality is checked first, then "default". Returns a
    dict: {can_kill, gun_tk, gun_label, note}. Smoke / gun-not-in-table / out-of-range
    return can_kill=False with a note and gun_tk=None.
    """
    t = _load()
    tk = t["to_kill"]
    bi = _bracket_index(rng, tk["range_brackets"])
    gun_size = _gun_size_key(mm, weapon_type)

    def no(note, label=None):
        return {"can_kill": False, "gun_tk": None, "gun_label": label or gun_size, "note": note}

    if ammo == "Smoke":
        return no("Smoke cannot kill")

    entry = tk["basic_tk"].get(gun_size)
    if entry is None:
        return no(f"Gun size {gun_size!r} is not in the AP To Kill table")

    if isinstance(entry, dict):
        if nationality in entry:
            base_tk, variant = entry[nationality], nationality
        elif "default" in entry:
            base_tk, variant = entry["default"], "all others"
        else:
            return no(f"Gun size {gun_size!r} is not available to {nationality or 'this nationality'}")
    else:
        base_tk, variant = entry, None
    gun_label = f"{gun_size} ({variant})" if variant else gun_size

    bucket = _case_d_bucket(tk, mm)
    case_d_mod = tk["case_d"][bucket]["drm"][bi]
    bucket_label = {"le25": "≤25mm", "mid": "37-57mm", "ge65": "≥65mm"}[bucket]
    if case_d_mod is None:
        return no(f"NA — out of To Kill range for a {bucket_label} gun", gun_label)

    return {"can_kill": True, "gun_tk": base_tk + case_d_mod, "gun_label": gun_label, "note": None}


def _kill_dist(ftk: int, tk_drm: int) -> Dict[str, float]:
    """
    C7.7 outcome category probabilities for a single To Kill DR vs a Final TK#.
    eff = 2d6 + tk_drm:
        eff ≤ ftk-1 → burn_elim   (Burning wreck + Eliminated, lumped)
        eff == ftk  → mid          (Immobilized on hull / Shock on turret)
        eff == ftk+1 → pshock      (Possible Shock)
        eff ≥ ftk+2 → none
    """
    cat = {"burn_elim": 0, "mid": 0, "pshock": 0, "none": 0}
    for s, c in _TWO_DICE.items():
        eff = s + tk_drm
        if eff <= ftk - 1:
            cat["burn_elim"] += c
        elif eff == ftk:
            cat["mid"] += c
        elif eff == ftk + 1:
            cat["pshock"] += c
        else:
            cat["none"] += c
    return {k: v / 36 for k, v in cat.items()}


def _best_of_2(d: Dict[str, float]) -> Dict[str, float]:
    """Most-severe-of-two outcomes (severity burn_elim > mid > pshock > none)."""
    pE, pM, pP, pN = d["burn_elim"], d["mid"], d["pshock"], d["none"]
    best_e = 1 - (1 - pE) ** 2
    best_m = 1 - (1 - pE - pM) ** 2 - best_e
    best_p = 1 - (1 - pE - pM - pP) ** 2 - best_e - best_m
    return {"burn_elim": best_e, "mid": max(0.0, best_m), "pshock": max(0.0, best_p), "none": pN ** 2}


OUTCOME_KEYS = ["burn_elim", "imob", "shock", "pshock"]
_BRANCH_LABELS = {"critical": "Critical Hit", "multiple": "Multiple Hit", "normal": "Normal Hit"}


def compute_flow(
    target_type: str,
    rng: int,
    weapon_type: str,
    ammo: str,
    mm: int,
    nationality: str = "",
    th_drm: int = 0,
    tk_drm: int = 0,
    hull_af: int = 0,
    turret_af: int = 0,
) -> Dict[str, Any]:
    """Flow-tree resolution: To Hit branches → per-branch To Kill outcome conditionals."""
    t = _load()
    _validate(t, target_type, weapon_type, ammo, nationality, rng, mm)
    th_drm = max(-8, min(8, th_drm))
    tk_drm = max(-8, min(8, tk_drm))

    final_th, basic_th, th_mods = final_to_hit(target_type, rng, weapon_type, ammo, mm, nationality, th_drm)
    g = gun_to_kill(mm, rng, ammo, weapon_type, nationality)
    can_kill = g["can_kill"]
    gun_tk = g["gun_tk"]

    # Multiple Hits (rule 3.8) only for 15–40mm guns vs Vehicle/Infantry.
    is_mult_gun = (15 <= mm <= 40) and target_type != "area"

    ftk_hull = ftk_turret = ftk_crit = None
    if can_kill:
        ftk_hull = gun_tk - hull_af
        ftk_turret = gun_tk - turret_af
        ftk_crit = 2 * gun_tk - hull_af  # Critical doubles the gun's TK#, hull only

    # Classify the 36 ordered combos. Facing: turret if d1 < d2, else hull.
    combos = {"critical": [], "multiple": [], "normal": []}
    miss = 0
    for d1 in range(1, 7):
        for d2 in range(1, 7):
            s = d1 + d2
            if not (s == 2 or (s <= final_th and s != 12)):
                miss += 1
                continue
            facing = "turret" if d1 < d2 else "hull"
            combo = {"d1": d1, "d2": d2, "facing": facing}
            if d1 == d2 and s == 2:
                combos["critical"].append(combo)
            elif d1 == d2 and is_mult_gun:
                combos["multiple"].append(combo)
            else:
                combos["normal"].append(combo)

    def cond_for(key: str, br_combos: List[Dict[str, Any]]) -> Dict[str, float]:
        res = {k: 0.0 for k in OUTCOME_KEYS}
        n = len(br_combos)
        if not can_kill or n == 0:
            return res
        if key == "critical":
            k = _kill_dist(ftk_crit, tk_drm)
            res.update(burn_elim=k["burn_elim"], imob=k["mid"], shock=0.0, pshock=k["pshock"])
        elif key == "multiple":
            k = _best_of_2(_kill_dist(ftk_hull, tk_drm))
            res.update(burn_elim=k["burn_elim"], imob=k["mid"], shock=0.0, pshock=k["pshock"])
        else:  # normal — blend hull / turret combos
            n_hull = sum(1 for c in br_combos if c["facing"] == "hull")
            n_tur = n - n_hull
            kh = _kill_dist(ftk_hull, tk_drm)
            kt = _kill_dist(ftk_turret, tk_drm)
            res["burn_elim"] = (n_hull * kh["burn_elim"] + n_tur * kt["burn_elim"]) / n
            res["imob"] = (n_hull * kh["mid"]) / n
            res["shock"] = (n_tur * kt["mid"]) / n
            res["pshock"] = (n_hull * kh["pshock"] + n_tur * kt["pshock"]) / n
        return {k: _round(v) for k, v in res.items()}

    order = ["critical"] + (["multiple"] if is_mult_gun else []) + ["normal"]
    branches = [{
        "key": key,
        "label": _BRANCH_LABELS[key],
        "count": len(combos[key]),
        "p": _round(len(combos[key]) / 36),
        "combos": combos[key],
        "cond": cond_for(key, combos[key]),
    } for key in order]

    return {
        "target_type": target_type,
        "nationality": nationality,
        "weapon_type": weapon_type,
        "ammo": ammo,
        "mm": mm,
        "range": rng,
        "th_drm": th_drm,
        "tk_drm": tk_drm,
        "final_th": final_th,
        "can_kill": can_kill,
        "gun_tk": gun_tk,
        "gun_label": g["gun_label"],
        "note": g["note"],
        "is_mult_gun": is_mult_gun,
        "ftk": {"hull": ftk_hull, "turret": ftk_turret, "crit": ftk_crit},
        "miss_p": _round(miss / 36),
        "branches": branches,
    }


def get_options() -> Dict[str, Any]:
    """Selectable input options for the UI."""
    t = _load()
    return {
        "target_types": t["target_types"],
        "weapon_types": t["weapon_types"],
        "ammo_types": t["ammo_types"],
        "nationalities": t["nationalities"],
    }
