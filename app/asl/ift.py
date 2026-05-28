"""
Infantry Fire Table (IFT) probability engine — deterministic, no LLM.

Given an FP column, a DRM, and whether cowering applies, enumerate all 36
two-die combinations and return the probability of each combat result.

Cowering: ASL resolves the attack on a 2-die DR. When the two dice come up
doubles and cowering applies, the FP column shifts one to the LEFT (weaker)
before the result is read. Doubles is a property of the individual dice, so
we enumerate all 36 ordered combinations rather than the 11 possible sums.
"""

import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, List

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


def compute_distribution(column: int, drm: int = 0, cowering: str = "none") -> Dict[str, Any]:
    """
    Probability of each IFT result for the given attack.

    Args:
        column: FP column — must be one of `valid_columns()`.
        drm: Total DR modifier (negative is favorable to the firer).
        cowering: "none", "regular" (doubles shift 1 column left), or "double"
                  (doubles shift 2 columns left, e.g. Conscripts). When the
                  shift moves left of the 1 FP column the attack falls off the
                  table → no effect.

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

    base_idx = columns.index(column)
    shift = COWERING_SHIFT[cowering]
    tally: Dict[str, int] = defaultdict(int)
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
                result = no_effect
            else:
                row_key = _dr_row_key(final_dr, dr_rows)
                result = results[row_key][col_idx]
            tally[result] += 1

    distribution = [
        {"result": r, "probability": round(c / 36, 4), "count": c}
        for r, c in tally.items()
    ]
    # Sort by probability desc, then result string for stable ordering.
    distribution.sort(key=lambda x: (-x["count"], x["result"]))

    return {
        "column": column,
        "drm": drm,
        "cowering": cowering,
        "distribution": distribution,
        "cowering_outcomes": cowering_count,
    }
