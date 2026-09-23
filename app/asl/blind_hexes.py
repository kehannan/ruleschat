"""
Deterministic ASL Blind Hex calculator (A6.4-.43, B10.23, B11.21).

Given a firer looking DOWN over one obstacle (a terrain obstacle on a hill,
a bare hill Crest Line, or a cliff hexside) at the hexes directly beyond it
along the LOS, derive - without any LLM in the loop - which of those hexes
are Blind Hexes, with a human-readable, rule-cited ledger of every step.

Rules (verified verbatim against the local eASLRB v3.14 text):

  * A6.4  - a firer above a full-level obstacle has a number of Blind Hexes
            directly behind it equal to the obstacle's full-level height
            (half-levels ignored); a hex at an elevation >= that height is
            never blind.
  * A6.41 - +1 Blind Hex for every full multiple of five hexes of range from
            the firer to the obstacle.
  * A6.42 - -1 Blind Hex for every full level of elevation advantage over the
            obstacle beyond the first, to a minimum of one [EXC: non-cliff
            Crest Lines may be reduced to none; B10.23].
  * A6.43 - a hex behind the obstacle that is LOWER than the obstacle's hex
            adds the elevation difference to the count (for a Blind Hex
            created solely by a Crest Line, only any difference > one); a
            hex that is HIGHER subtracts the difference.
  * B10.23 - a lower-level non-cliff Crest Line creates Blind Hexes to a
            higher viewer only if >= five hexes away [EXC: a drop of >= two
            levels creates at least one Blind Hex unless adjacent]. Nominal
            effect of a one-level Crest Line: 0 blind at range 1-4, 1 at
            5-9, 2 at 10-14, ...  Reducible to zero by A6.42.
  * B11.21 - Blind Hexes caused by a cliff hexside to a non-adjacent viewer
            can never be reduced below one regardless of elevation advantage.
  * Q&A B11.21 - "unit at Level 2 and a Level 1 cliff 5-9 hexes away ... one
            or two Blind Hexes along its LOS to a Level 0 Location?  A. Two."
            (so a cliff adds the FULL drop per A6.43, unlike a bare crest).

Two readings the rule text leaves open, resolved here and flagged in the
output's `assumptions`:

  * The minimums (A6.42 min 1, B11.21 min 1, the B10.23 EXC min 1) are
    applied to the per-hex total AFTER the A6.43 adjustment. The A6.43
    example forces this: a level-4 firer over the 2I3 woods must see H1,
    which only works if the min-1 floor is not added on top of A6.43's +1.
  * The B10.23 EXC floor (>= 2-level non-cliff drop) is applied after the
    A6.42 elevation-advantage reduction, so a large advantage cannot negate
    that one hex.

Every rulebook example (A6.42, A6.43, B10.23 A-G, the B11.21 Q&A and
B10.23's nominal range table) is reproduced in tests/test_blind_hexes.py.
"""
from typing import Any, Dict, List, Optional


def _int(name: str, value: Any, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, float):
        if value != int(value):
            raise ValueError(
                f"{name} must be a whole number of levels (half-levels are ignored per "
                f"A6.4 - round down), got {value!r}"
            )
        value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def compute_blind_hexes(
    firer_level: int,
    obstacle_hex_level: int,
    range_to_obstacle: int,
    hexes_behind: List[int],
    obstacle_height: int = 0,
    cliff: bool = False,
) -> Dict[str, Any]:
    """
    Which hexes directly behind one obstacle are Blind Hexes to a higher firer.

    Args:
        firer_level: Elevation level of the firer (full levels).
        obstacle_hex_level: Base (hill) level of the hex holding the obstacle;
            for a bare Crest Line or cliff, the level of the hex on the HIGH
            side of the crest.
        range_to_obstacle: Range in hexes from the firer to the obstacle hex
            (>= 1).
        hexes_behind: Elevation of each hex directly beyond the obstacle along
            the LOS, nearest first.
        obstacle_height: Full-level height of the terrain obstacle in that hex
            above its base level (woods / one-story building 1, two-story
            building 2, ...). 0 = a bare hill Crest Line or cliff hexside with
            no terrain obstacle. Half-levels are ignored (A6.4).
        cliff: The hexside the LOS exits the obstacle hex through is a cliff
            hexside (B11).

    Returns a dict with the per-hex verdicts (`hexes`), `blind_count`, the
    intermediate numbers, a `steps` list of rule-cited plain-English lines,
    and `assumptions`.
    """
    F = _int("firer_level", firer_level)
    O = _int("obstacle_hex_level", obstacle_hex_level)
    R = _int("range_to_obstacle", range_to_obstacle, minimum=1)
    H = _int("obstacle_height", obstacle_height, minimum=0)
    if not isinstance(hexes_behind, (list, tuple)) or not hexes_behind:
        raise ValueError("hexes_behind must be a non-empty list of elevations")
    T = [_int(f"hexes_behind[{i}]", t) for i, t in enumerate(hexes_behind)]
    cliff = bool(cliff)

    top = O + H
    bare_crest = H == 0 and not cliff
    kind = "cliff hexside" if (H == 0 and cliff) else (
        "bare Crest Line" if bare_crest else f"level {H} terrain obstacle"
    )
    if H > 0 and cliff:
        kind += " with a cliff hexside beyond it"

    steps: List[str] = []
    assumptions: List[str] = [
        "Only this one obstacle is considered; other obstacles/hindrances along "
        "the LOS and the B10.2 rule that a LOS to a lower level may never recross "
        "a Crest Line of the firer's own level or higher are not modeled.",
        "Elevations are full levels; half-level obstacles (walls, hedges, rubble, "
        "wrecks) are ignored for Blind Hex purposes (A6.4).",
    ]
    result: Dict[str, Any] = {
        "inputs": {
            "firer_level": F,
            "obstacle_hex_level": O,
            "obstacle_height": H,
            "obstacle_top": top,
            "range_to_obstacle": R,
            "hexes_behind": T,
            "cliff": cliff,
        },
        "obstacle_kind": kind,
        "sees_over_obstacle": F > top,
        "steps": steps,
        "assumptions": assumptions,
        "rules": ["A6.4", "A6.41", "A6.42", "A6.43", "B10.23"] + (["B11.21"] if cliff else []),
    }

    def _finish(hexes: List[Dict[str, Any]]) -> Dict[str, Any]:
        result["hexes"] = hexes
        result["blind_count"] = sum(1 for h in hexes if h["blind"])
        result["blind_hexes"] = [h["index"] for h in hexes if h["blind"]]
        result["visible_hexes"] = [h["index"] for h in hexes if not h["blind"]]
        return result

    # ---- 1. Can the firer see over the obstacle at all? (A6.2, B10.2, B10.22)
    steps.append(
        f"Obstacle top = hex level {O} + terrain height {H} = level {top}; firer is at "
        f"level {F}."
    )
    if F <= top:
        steps.append(
            f"Firer level {F} is not above the obstacle top (level {top}), so LOS does "
            f"not pass over it: every hex beyond that is lower than level {top} is "
            f"blocked outright (A6.2, B10.2), not a Blind Hex calculation."
        )
        hexes = []
        for i, t in enumerate(T, 1):
            blocked = t < top
            hexes.append({
                "index": i, "elevation": t, "adjustment": 0, "threshold": None,
                "blind": blocked,
                "reason": ("blocked - lower than the obstacle top (A6.2)" if blocked
                           else "at/above the obstacle top - not blocked by it (B10.22)"),
            })
        result.update({"base_blind": None, "range_bonus": None,
                       "elevation_advantage": F - top, "advantage_reduction": None})
        return _finish(hexes)

    # ---- 2. Adjacent bare crest / cliff creates nothing (B10.23 EXC, B11.21, B10.2 EXC)
    if H == 0 and R == 1:
        steps.append(
            "The Crest Line/cliff is in a hex adjacent to the firer: an adjacent "
            "Crest Line never creates Blind Hexes (B10.23 'unless adjacent'; B11.21 "
            "'non-adjacent viewer'; B10.2 EXC)."
        )
        hexes = [{
            "index": i, "elevation": t, "adjustment": 0, "threshold": 0, "blind": False,
            "reason": "adjacent Crest Line/cliff creates no Blind Hexes (B10.23, B11.21)",
        } for i, t in enumerate(T, 1)]
        result.update({"base_blind": 0, "range_bonus": 0,
                       "elevation_advantage": F - top, "advantage_reduction": 0})
        return _finish(hexes)

    # ---- 3-5. Base count, range bonus, elevation-advantage reduction
    A = F - top
    range_bonus = R // 5
    reduction = max(0, A - 1)
    N = H + range_bonus - reduction
    if H > 0:
        steps.append(
            f"Start with the obstacle's full-level height: {H} Blind Hex(es) (A6.4)."
        )
    else:
        steps.append(
            "A bare Crest Line/cliff has no terrain height, so start at 0 (B10.23: a "
            "one-level Crest Line creates no Blind Hexes at range 1-4)."
        )
    steps.append(
        f"Range {R} to the obstacle: +{range_bonus} for every full five hexes (A6.41)."
    )
    steps.append(
        f"Elevation advantage over the obstacle top = {F} - {top} = {A}: -{reduction} "
        f"for every level beyond the first (A6.42)."
    )
    steps.append(f"Running count = {H} + {range_bonus} - {reduction} = {N}.")

    # ---- 6-8. Per-hex A6.43 adjustment, minimums, verdict
    drop_first = O - T[0]
    exc_floor = bare_crest and drop_first >= 2
    if H > 0:
        floor_note = "at least 1 for a terrain obstacle (A6.42)"
    elif cliff:
        floor_note = "at least 1 for a non-adjacent cliff (B11.21)"
    elif exc_floor:
        floor_note = (f"at least 1 because the Crest Line drops {drop_first} levels "
                      f"(B10.23 EXC)")
    else:
        floor_note = "may be reduced to 0 for a non-cliff Crest Line (B10.23)"
    floor = 1 if (H > 0 or cliff or exc_floor) else 0
    steps.append(f"Minimum: {floor_note}.")

    hexes: List[Dict[str, Any]] = []
    for i, t in enumerate(T, 1):
        if t >= top:
            hexes.append({
                "index": i, "elevation": t, "adjustment": None, "threshold": None,
                "blind": False,
                "reason": f"at level {t} >= obstacle top {top}: never a Blind Hex (A6.4)",
            })
            steps.append(
                f"Hex {i} (level {t}) is at or above the obstacle top: never blind (A6.4)."
            )
            continue
        if t < O:
            diff = O - t
            adj = diff - (1 if bare_crest else 0)
            why = (f"{diff} level(s) below the obstacle hex: +{adj} (A6.43"
                   + ("; first level of a Crest Line drop is free" if bare_crest else "")
                   + ")")
        elif t > O:
            adj = -(t - O)
            why = f"{t - O} level(s) above the obstacle hex: {adj} (A6.43)"
        else:
            adj = 0
            why = "same level as the obstacle hex: no A6.43 adjustment"
        threshold = max(N + adj, floor)
        blind = i <= threshold
        hexes.append({
            "index": i, "elevation": t, "adjustment": adj, "threshold": threshold,
            "blind": blind,
            "reason": why + f"; Blind Hex count {threshold} vs position {i}",
        })
        steps.append(
            f"Hex {i} (level {t}): {why}; max({N} {'+' if adj >= 0 else '-'} {abs(adj)}, "
            f"{floor}) = {threshold} Blind Hexes -> hex {i} is "
            f"{'BLIND' if blind else 'visible'}."
        )

    result.update({
        "base_blind": H,
        "range_bonus": range_bonus,
        "elevation_advantage": A,
        "advantage_reduction": reduction,
        "count_before_terrain_behind": N,
        "minimum": floor,
    })
    assumptions.append(
        "Minimums (A6.42 / B11.21 / B10.23 EXC) are applied to the per-hex total after "
        "the A6.43 terrain-behind adjustment, as the A6.43 example requires."
    )
    if exc_floor:
        assumptions.append(
            "The B10.23 EXC's guaranteed Blind Hex for a >= 2-level drop is treated as "
            "not negatable by elevation advantage; the rule text is ambiguous on that."
        )
    return _finish(hexes)
