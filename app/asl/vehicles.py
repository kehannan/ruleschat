"""
Vehicle lookup over the Chapter H listings (data/rulebook/vehicles.json).

The table is built by scripts/extract_vehicle_listings.py from the eASLRB and
is gitignored (copyrighted). Like rules_lookup, this never raises on a missing
store: `find` returns an error and the TH/TK tools fall back to user-supplied
AF/gun values.

`find("Panther")` resolves nicknames, spacing/punctuation variants and unique
prefixes to one listing row; ambiguous names come back as candidates.
"""
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_PATH = Path(__file__).resolve().parents[2] / "data" / "rulebook" / "vehicles.json"
_lock = threading.Lock()
_vehicles: Optional[List[Dict[str, Any]]] = None

# Common names -> listing names (the listings use the official designations).
ALIASES = {
    "panther": ["PzKpfw VG", "PzKpfw VD"],
    "pantherg": ["PzKpfw VG"],
    "pantherd": ["PzKpfw VD"],
    "tiger": ["PzKpfw VIE"],
    "tigeri": ["PzKpfw VIE"],
    "tigere": ["PzKpfw VIE"],
    "kingtiger": ["PzKpfw VIB"],
    "tigerii": ["PzKpfw VIB"],
    "konigstiger": ["PzKpfw VIB"],
    "koenigstiger": ["PzKpfw VIB"],
    "jagdpanther": ["JgdPz V"],
    "jagdtiger": ["JgdPz VI"],
    "firefly": ["Sherman VC(a)", "Sherman IIC(a)"],
    # U.S. Shermans are listed by designation; a bare "Sherman" must not
    # prefix-match the lone "Sherman Crab".
    "sherman": ["M4", "M4A1", "M4A2", "M4A2 (L)", "M4A3", "M4A3(75)W", "M4A1(76)W",
                "M4A3(76)W", "M4(105)", "M4A3(105)", "M4A3E2", "M4A3E8",
                "Sherman II(a)", "Sherman III(a)", "Sherman V(a)", "Sherman IIA(a)",
                "Sherman IIC(a)", "Sherman VC(a)", "Sherman IB(a)"],
    "jumbo": ["M4A3E2", "M4A3E2 (L)"],
    "hellcat": ["M18 GMC"],
    "wolverine": ["M10 GMC", "Wolverine(a)"],
    "jackson": ["M36 GMC"],
    "pershing": ["M26"],
    "chaffee": ["M24"],
    "priest": ["M7 HMC"],
    "easyeight": ["M4A3E8"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load(path: Optional[str] = None) -> List[Dict[str, Any]]:
    global _vehicles
    with _lock:
        if _vehicles is None or path:
            p = Path(path) if path else _PATH
            try:
                _vehicles = json.loads(p.read_text())["vehicles"]
            except (OSError, ValueError, KeyError) as e:
                logging.warning("vehicles: listing store unavailable (%s): %s", p, e)
                _vehicles = []
        return _vehicles


def reset() -> None:
    global _vehicles
    with _lock:
        _vehicles = None


def _summary(v: Dict[str, Any]) -> str:
    kind = f" {v['type']}" if v.get("type") else ""
    return f"{v['name']}{kind} ({v['nationality']})"


def find(query: str, nationality: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a vehicle name -> {"vehicle": row} | {"candidates": [...]} | {"error"}."""
    rows = load()
    if not rows:
        return {"error": "vehicle listings not built (scripts/extract_vehicle_listings.py); "
                         "supply the AF / gun yourself"}
    q = _norm(query)
    if not q:
        return {"error": "empty vehicle name"}
    pool = rows
    if nationality:
        nat = [v for v in rows if v["nationality"].lower() == nationality.lower()]
        pool = nat or rows

    def pick(matches):
        if not matches:
            return None
        if len(matches) == 1:
            return {"vehicle": matches[0]}
        # Same stats under several listings (captured vehicles etc.) are fine.
        keys = {(m["hull_front"], m["hull_side"], m["turret_front"], m["turret_side"],
                 m["gun"], m["size"]) for m in matches}
        if len(keys) == 1:
            return {"vehicle": matches[0],
                    "note": f"{len(matches)} listings share these stats: "
                            + ", ".join(_summary(m) for m in matches)}
        return {"candidates": [_summary(m) for m in matches[:12]]}

    exact = [v for v in pool if _norm(v["name"]) == q]
    if exact:
        return pick(exact)
    # "M3 MT": name plus listing type code
    typed = [v for v in pool if v.get("type") and _norm(v["name"] + v["type"]) == q]
    if typed:
        return pick(typed)
    if q in ALIASES:
        names = {_norm(n) for n in ALIASES[q]}
        res = pick([v for v in pool if _norm(v["name"]) in names]
                   or [v for v in rows if _norm(v["name"]) in names])
        if res:
            res["alias"] = query
            return res
    # Tolerate a dropped "(a)" / "(r)" capture suffix and unique prefixes.
    loose = [v for v in pool if re.sub(r"[a-z]$", "", _norm(v["name"])) == q
             or _norm(re.sub(r"\([a-z]\)", "", v["name"])) == q]
    if loose:
        return pick(loose)
    prefix = [v for v in pool if _norm(v["name"]).startswith(q)]
    res = pick(prefix)
    if res:
        return res
    contains = [v for v in pool if q in _norm(v["name"])]
    return pick(contains) or {"error": f"no vehicle listing matches {query!r}"}


def armor_for(v: Dict[str, Any], facing: str) -> Dict[str, Any]:
    """Hull/turret AF of the Target Facing fired on (side and rear share one AF)."""
    side = facing in ("side", "rear")
    return {
        "hull_af": v["hull_side"] if side else v["hull_front"],
        "turret_af": v["turret_side"] if side else v["turret_front"],
    }
