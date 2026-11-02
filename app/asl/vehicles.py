"""
Vehicle and Gun lookup over the Chapter H listings (data/rulebook/vehicles.json).

The table is built by scripts/extract_vehicle_listings.py from the eASLRB and
is gitignored (copyrighted). Hand corrections live in data/listing_overrides.json
(committed, applied at load) and nicknames in data/listing_aliases.json. Like rules_lookup, this never raises on a missing
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
_vehicles: Optional[Dict[str, List[Dict[str, Any]]]] = None

_DATA = Path(__file__).resolve().parent / "data"
ALIASES_PATH = _DATA / "listing_aliases.json"
OVERRIDES_PATH = _DATA / "listing_overrides.json"


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    # VASL counters say "PzKw IVJ"; the listings say "PzKpfw IVJ".
    return s.replace("pdr", "pounder").replace("pzkw", "pzkpfw")


def _aliases(kind: str) -> Dict[str, List[str]]:
    """Common names from listing_aliases.json, keyed by normalized name."""
    try:
        data = json.loads(ALIASES_PATH.read_text())[kind]
    except (OSError, ValueError, KeyError) as e:
        logging.warning("vehicles: aliases unavailable (%s): %s", ALIASES_PATH, e)
        return {}
    return {_norm(k): v for k, v in data.items()}


def load(path: Optional[str] = None) -> List[Dict[str, Any]]:
    return _load(path)["vehicles"]


def load_guns(path: Optional[str] = None) -> List[Dict[str, Any]]:
    return _load(path)["guns"]


def _load(path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    global _vehicles
    with _lock:
        if _vehicles is None or path:
            p = Path(path) if path else _PATH
            try:
                data = json.loads(p.read_text())
                _vehicles = _apply_overrides({"vehicles": data["vehicles"],
                                              "guns": data.get("guns", [])})
            except (OSError, ValueError, KeyError) as e:
                logging.warning("vehicles: listing store unavailable (%s): %s", p, e)
                # Don't pin the empty result: a store copied in later is picked up.
                return {"vehicles": [], "guns": []}
        return _vehicles


def _apply_overrides(table: Dict[str, List[Dict[str, Any]]],
                     path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Apply listing_overrides.json: field corrections and added rows."""
    p = path or OVERRIDES_PATH
    try:
        fixes = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        logging.warning("vehicles: overrides unavailable (%s): %s", p, e)
        return table
    for kind in ("vehicles", "guns"):
        rows = table[kind]
        for fix in fixes.get(kind, []):
            hits = [r for r in rows if r["name"] == fix.get("name") and
                    (not fix.get("nationality") or r["nationality"] == fix["nationality"])]
            if not hits:
                logging.warning("vehicles: override matches no %s row: %s", kind, fix.get("name"))
            for r in hits:
                r.update(fix.get("set", {}))
                r["override"] = fix.get("reason", "listing_overrides.json")
        for row in fixes.get(f"add_{kind}", []):
            rows.append({**row, "override": row.get("override", "added in listing_overrides.json")})
    return table


def reset() -> None:
    global _vehicles
    with _lock:
        _vehicles = None


def _summary(v: Dict[str, Any]) -> str:
    kind = f" {v['type']}" if v.get("type") else ""
    return f"{v['name']}{kind} ({v['nationality']})"


def find(query: str, nationality: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a vehicle name -> {"vehicle": row} | {"candidates": [...]} | {"error"}."""
    return _find(load(), query, nationality, _aliases("vehicles"), "vehicle",
                 lambda m: (m["hull_front"], m["hull_side"], m["turret_front"],
                            m["turret_side"], m["gun"], m["size"]))


def find_gun(query: str, nationality: Optional[str] = None) -> Dict[str, Any]:
    """Resolve an ordnance (Gun) name -> {"gun": row} | {"candidates": [...]} | {"error"}."""
    res = _find(load_guns(), query, nationality, _aliases("guns"), "gun",
                lambda m: (m["gun"], m["rof"], m["b_number"], m["size"]))
    if "vehicle" in res:
        res["gun"] = res.pop("vehicle")
    return res


def _find(rows, query, nationality, aliases, what, stats_key) -> Dict[str, Any]:
    if not rows:
        return {"error": f"{what} listings not built (scripts/extract_vehicle_listings.py); "
                         "supply the AF / gun yourself"}
    q = _norm(query)
    if not q:
        return {"error": f"empty {what} name"}
    pool = rows
    if nationality:
        nat = [v for v in rows if v["nationality"].lower() == nationality.lower()]
        pool = nat or rows

    def pick(matches):
        if not matches:
            return None
        if len(matches) == 1:
            return {"vehicle": matches[0]}
        # Same stats under several listings (captured equipment etc.) are fine.
        if len({stats_key(m) for m in matches}) == 1:
            return {"vehicle": matches[0],
                    "note": f"{len(matches)} listings share these stats: "
                            + ", ".join(_summary(m) for m in matches)}
        return {"candidates": [_summary(m) for m in matches[:12]]}

    def graded(res, how):
        if res:
            res["match"] = how
        return res

    exact = [v for v in pool if _norm(v["name"]) == q]
    if exact:
        return graded(pick(exact), "exact")
    # "M3 MT": name plus listing type code
    typed = [v for v in pool if v.get("type") and _norm(v["name"] + v["type"]) == q]
    if typed:
        return graded(pick(typed), "exact")
    if q in aliases:
        names = {_norm(n) for n in aliases[q]}
        res = pick([v for v in pool if _norm(v["name"]) in names]
                   or [v for v in rows if _norm(v["name"]) in names])
        if res:
            res["alias"] = query
            return graded(res, "alias")
    # Tolerate a dropped "(a)" / "(r)" capture suffix and unique prefixes.
    loose = [v for v in pool if re.sub(r"[a-z]$", "", _norm(v["name"])) == q
             or _norm(re.sub(r"\([a-z]\)", "", v["name"])) == q]
    if loose:
        return graded(pick(loose), "exact")
    prefix = [v for v in pool if _norm(v["name"]).startswith(q)]
    res = pick(prefix)
    if res:
        return graded(res, "partial")
    contains = [v for v in pool if q in _norm(v["name"])]
    return graded(pick(contains), "partial") or {"error": f"no {what} listing matches {query!r}"}


def armor_for(v: Dict[str, Any], facing: str) -> Dict[str, Any]:
    """Hull/turret AF of the Target Facing fired on (side and rear share one AF)."""
    side = facing in ("side", "rear")
    return {
        "hull_af": v["hull_side"] if side else v["hull_front"],
        "turret_af": v["turret_side"] if side else v["turret_front"],
    }
