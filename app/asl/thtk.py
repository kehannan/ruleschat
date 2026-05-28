"""
To Hit / To Kill (TH/TK) probability engine — deterministic, no LLM.

Ordnance combat, ASL Chapter C. Given a firing Gun (caliber, barrel class, ammo,
nationality) and a target (type, range), compute:
  - the To Hit number, the modifiers applied, and the chance to hit;
  - the To Kill number, the modifiers applied, and the chance to kill.

Both probabilities come from the 2d6 distribution. A To Hit dr of 2 always hits
and a dr of 12 always misses, per the rules. Target Armor is NOT subtracted on the
To Kill side (no armor input) — the raw TK# is reported so the user applies armor
themselves (each armor point = -1 to the TK#).

All table data lives in `thtk_tables.json` and is a first-pass transcription pending
human verification — see that file's `_note`.
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


def prob_le(n: int) -> float:
    """P(2d6 <= n), plain cumulative. 0.0 below 2, 1.0 at/above 12."""
    return sum(c for s, c in _TWO_DICE.items() if s <= n) / 36


def prob_hit(th: int) -> float:
    """
    Chance a 2d6 To Hit dr succeeds against a To Hit number `th`.

    ASL: an original dr of 2 always hits, a dr of 12 always misses, regardless of
    the modified TH#. So a TH# >= 12 still misses on boxcars (35/36) and a TH# <= 1
    still hits on snake eyes (1/36).
    """
    count = sum(c for s, c in _TWO_DICE.items() if s == 2 or (s <= th and s != 12))
    return count / 36


def _range_bracket_index(rng: int) -> int:
    brackets = _load()["range_brackets"]
    for i, b in enumerate(brackets):
        if rng <= b["max"]:
            return i
    return len(brackets) - 1


def _caliber_row(mm: int) -> str:
    """Largest To Kill caliber row <= mm (clamped to the smallest row)."""
    rows: List[int] = _load()["to_kill"]["caliber_rows"]
    chosen = rows[0]
    for r in rows:
        if mm >= r:
            chosen = r
    return str(chosen)


def _round(p: float) -> float:
    return round(p, 4)


def compute_to_hit(
    target_type: str,
    rng: int,
    weapon_type: str,
    ammo: str,
    mm: int,
    nationality: str = "",
    hit_drm: int = 0,
) -> Dict[str, Any]:
    """
    To Hit number, itemized modifiers, and hit probability.

    Basic TH# (C3) depends on target type, range band, and nationality (German
    optics use the higher column). C4 modifications are added to the basic number
    (positive = easier): the weapon barrel class, the ammo, and the small-gun size
    mods (<=57mm and <=40mm STACK). The Hit Determination DRM is a die modifier
    (positive = harder) and is subtracted. So:

        Final TH# = basic + weapon + ammo + size mods - hit_drm
    """
    t = _load()
    th = t["to_hit"]

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

    bi = _range_bracket_index(rng)
    bracket = t["range_brackets"][bi]

    nat_class = "German" if nationality == th["german_nationality"] else "Other"
    basic = th["basic"][target_type][nat_class][bi]

    mods = th["mods"]
    # Each entry is a signed contribution to the To Hit number.
    modifiers = [{"label": f"{weapon_type} weapon", "drm": mods["weapon_type"][weapon_type][bi]}]

    ammo_val = mods["ammo"][ammo][bi]
    if ammo != "AP/HE" or ammo_val != 0:
        modifiers.append({"label": f"{ammo} ammo", "drm": ammo_val})

    for spec in (mods["size"]["le57"], mods["size"]["le40"]):
        if mm <= spec["max_mm"]:
            modifiers.append({"label": f"≤{spec['max_mm']}mm gun", "drm": spec["drm"][bi]})

    if hit_drm:
        modifiers.append({"label": "Hit determination DRM", "drm": -hit_drm})

    final_th = basic + sum(m["drm"] for m in modifiers)

    return {
        "target_type": target_type,
        "nationality": nationality,
        "nationality_class": nat_class,
        "range": rng,
        "range_bracket": bracket["label"],
        "weapon_type": weapon_type,
        "ammo": ammo,
        "mm": mm,
        "basic_th": basic,
        "modifiers": modifiers,
        "final_th": final_th,
        "hit_prob": _round(prob_hit(final_th)),
    }


def compute_to_kill(
    mm: int,
    rng: int,
    ammo: str,
) -> Dict[str, Any]:
    """
    To Kill number, itemized modifiers, and kill probability.

    Final TK# = base_tk[caliber_row][range_bracket] + ammo modifier. Smoke cannot
    kill. Target Armor is not subtracted (no armor input) — the raw TK# is returned.
    """
    t = _load()
    tk = t["to_kill"]

    if ammo not in t["ammo_types"]:
        raise ValueError(f"Invalid ammo {ammo!r}. One of {t['ammo_types']}.")
    if mm <= 0:
        raise ValueError(f"Invalid weapon size {mm!r}mm. Must be > 0.")
    if rng < 0:
        raise ValueError(f"Invalid range {rng!r}. Must be >= 0.")

    bi = _range_bracket_index(rng)
    bracket = t["range_brackets"][bi]
    row = _caliber_row(mm)
    base_tk = tk["tk"][row][bi]

    ammo_mod_table = tk["ammo_mod"].get(ammo)
    if ammo_mod_table is None:
        # Smoke (or any null row) cannot kill.
        return {
            "mm": mm,
            "caliber_row": int(row),
            "range": rng,
            "range_bracket": bracket["label"],
            "ammo": ammo,
            "can_kill": False,
            "base_tk": base_tk,
            "modifiers": [],
            "final_tk": None,
            "kill_prob": 0.0,
        }

    ammo_mod = ammo_mod_table[bi]
    modifiers = [{"label": f"{ammo} ammo", "drm": ammo_mod}]
    final_tk = base_tk + ammo_mod

    return {
        "mm": mm,
        "caliber_row": int(row),
        "range": rng,
        "range_bracket": bracket["label"],
        "ammo": ammo,
        "can_kill": True,
        "base_tk": base_tk,
        "modifiers": modifiers,
        "final_tk": final_tk,
        "kill_prob": _round(prob_le(final_tk)),
    }


def compute(
    target_type: str,
    rng: int,
    weapon_type: str,
    ammo: str,
    mm: int,
    nationality: str = "",
    hit_drm: int = 0,
) -> Dict[str, Any]:
    """Full TH + TK result for one ordnance attack."""
    t = _load()
    if nationality and nationality not in t["nationalities"]:
        raise ValueError(f"Invalid nationality {nationality!r}. One of {t['nationalities']}.")
    return {
        "nationality": nationality,
        "to_hit": compute_to_hit(target_type, rng, weapon_type, ammo, mm, nationality, hit_drm),
        "to_kill": compute_to_kill(mm, rng, ammo),
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
