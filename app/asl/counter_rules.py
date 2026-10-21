"""What each map counter does to a fire attack — one table, not code paths.

The VASL extension (>= 0.6) reports every non-unit counter in the firer,
target and LOS hexes generically (name, hexside position, rotation, arrow
layer). `classify()` decides what each one is:

  vasl_los    VASL's own LOS engine already accounts for it (SMOKE, wrecks,
              OBA, blazes: hindrances; rubble, bridges, crests, building
              levels...: map terrain). Taken from VASL's CounterMetadata.xml
              (data/vasl_counter_metadata.json), so it tracks VASL.
  hexside_wall  a wall-like hexside obstacle VASL's LOS IGNORES: +TEM when the
              LOS enters the target across it, a Half-Level Obstacle to
              same-level LOS elsewhere (B9.2). The hexside is the one the
              counter sits on, else the one its arrow points at.
  handled     the resolver already reads it from the save (entrenchments,
              concealment).
  no_effect   no effect on LOS or TEM (status markers, wire, mines...).
  unmodeled   anything else: listed in the answer as "not modelled" so a
              new edge case shows up the first time it appears.

Adding an edge case = adding a row here (+ a test), not new resolver code.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# First match wins. `match` is a case-insensitive regex against the counter
# name. For hexside_wall: `arrow_base` is the art's arrow bearing at
# rotation 0 (0 = up, clockwise); an "Arrow" layer at level 2 flips it up.
COUNTER_RULES = [
    dict(match=r"^roadblock", kind="hexside_wall", label="Roadblock", tem=2,
         rule="B29.2-.3", arrow_base=180, road_hexside=True),
    # Entrenchments and concealment are read from the save by the resolver.
    dict(match=r"(foxhole|trench|a-t ditch)", kind="handled", rule="B27"),
    dict(match=r"^(\?|concealed|conceal)", kind="handled", rule="A12"),
    # No effect on LOS or on the target's TEM.
    dict(match=r"^(prep fire|first fire|final fire|bounding fire|fire lane|"
               r"cx|pin|dm|motion|moved|labor|ti|acquired|acquisition|"
               r"low crawl|ambush|melee|cc|hip|encircled|dash|sniper|"
               r"residual|interdiction|turn|phase|rally|leader|hero|"
               r"evade|reverse|shock|stun|recall|bog|mired|immobilized|"
               r"burning|ammo|malf|disabled|repair|"
               r"wire|panji|a-p mine|a-t mine|minefield|known minefield|"
               r"mines|daisy|set dc|dc)\b",
         kind="no_effect"),
]


@lru_cache(maxsize=1)
def _vasl_metadata() -> Dict[str, str]:
    path = Path(__file__).parent / "data" / "vasl_counter_metadata.json"
    return json.loads(path.read_text())["counters"]


def classify(name: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Counter name -> (kind, rule entry or None)."""
    n = (name or "").strip()
    for entry in COUNTER_RULES:
        if re.search(entry["match"], n, re.IGNORECASE):
            return entry["kind"], entry
    # VASL matches its metadata by name prefix; mirror that.
    for prefix, vtype in _vasl_metadata().items():
        if n.startswith(prefix) and vtype != "ignore":
            return "vasl_los", {"vasl_type": vtype}
    return "unmodeled", None


def hexside_from_counter(counter: Dict[str, Any], hexinfo: Dict[str, Any],
                         entry: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(neighbor hex the counter's hexside faces, how it was found).

    Sitting on a hexside wins; otherwise read the arrow: screen bearing =
    arrow_base - rotation angle (VASSAL draws FreeRotator at -angle), with an
    "Arrow" layer at level 2 pointing up instead and level 3 = no arrow.
    """
    sides = {s.get("side"): s for s in hexinfo.get("sides") or []
             if isinstance(s, dict)}
    side = counter.get("side")
    if isinstance(side, int) and side >= 0 and side in sides:
        return sides[side].get("neighbor"), "position"
    base = entry.get("arrow_base")
    level = counter.get("arrow_level")
    if level not in (None, "", "null", "None"):
        if str(level).strip() == "2":
            base = 0
        elif str(level).strip() != "1":
            return None, "no arrow"
    angle = counter.get("angle")
    if base is None:
        return None, "unknown"
    bearing = base - float(angle or 0)
    best, best_score = None, None
    for s in sides.values():
        if s.get("bearing") is None or not s.get("neighbor"):
            continue
        d = abs(((float(s["bearing"]) - bearing) % 360 + 540) % 360 - 180)
        if entry.get("road_hexside") and \
                "road" not in str(s.get("location_terrain", "")).lower():
            d += 20  # tie-break: B29.1 roadblocks obstruct a road hexside
        if best_score is None or d < best_score:
            best, best_score = s.get("neighbor"), d
    return best, "arrow"
