"""
Deterministic ASL fire-attack resolver over parsed .vsav board state.

`resolve_attack(state, firing_hex, target_hex, ...)` takes the dict produced
by `app.services.vsav_service.parse_vsav` and derives — without any LLM in
the loop — everything `app.asl.ift.compute_attack` needs to resolve an IFT
attack between two hexes:

  * which units in the firing hex are eligible to fire (side inference,
    BROKEN/enemy/ordnance/already-fired exclusions, SW manning),
  * each firer's printed FP / Normal Range (parsed from counter names, plus
    a static SW table read from VASL 6.7.3 counter art),
  * per-squad Assault Fire (A7.36) / Spraying Fire (A7.34) capability — the
    counter's underscored FP/Range — looked up via app.asl.unit_capabilities
    from the unit's counter-art path (parse_vsav's `art` field, exact) or a
    (nationality, strength-string, class) fallback. Assault Fire is applied
    in the ADVANCING phase only; Spraying Fire is surfaced as a note (it
    needs a two-Location target choice and is never auto-applied),
  * the hex range, with A7.21 PBF / A7.211 TPBF and A7.22 long-range
    handling,
  * an itemized, rule-cited DRM ledger (entrenchment vs terrain TEM,
    leadership, CX, encirclement),
  * cowering derivation inputs (A7.9 incl. the Finnish exemption),
  * an explicit `assumptions` list for everything the save cannot tell us
    (LOS, hindrances along the path, overlays, ...).

The motivating failure case: given the Hazmo fixture, an LLM asked to
resolve "units in 57-H9 prep-fire at 57-H8" answered 8 FP +1 DRM. The
correct answer — 16 FP (6+2 doubled by A7.21 PBF at range 1) and +2 DRM
(B27.3 foxhole TEM) — falls straight out of the parsed state. This module
derives those inputs deterministically so the model presents them instead
of inventing them.

Every rule value below is data with a rule-id citation. Values verified
against the local eASLRB text (static/rulebook/eASLRB_v3_14) are cited
plainly; anything encoded with less than full confidence is tagged
``# VERIFY`` and consolidated in the block immediately below.

# ============================================================================
# VERIFY — consolidated list of values/rule-ids encoded with less than full
# confidence. Everything else in this module was checked verbatim against
# the eASLRB v3.14 text or read from VASL 6.7.3 counter art.
#
#  V1. Generic SW fallback FP/range for a nationality missing from SW_TABLE:
#      LMG 2-6, MMG 4-10, HMG 6-12, ATR 1-12. (Matches the most common
#      counters; minors/others may differ.)
#  V2. Huts TEM +1 (G5.x). Encoded +1; rule id not verified locally.
#  V3. Light Jungle treated as Woods, TEM +1 (G2.1). Not verified locally.
#  V4. Palm Trees treated per orchard rules, TEM 0 (G4). Not verified locally.
#  V5. Graveyard TEM +1 (B18). Not verified locally.
#  V6. Marketplace hexes treated as their building type (stone +3 / wooden
#      +2); B23.733 lists exceptions we don't model.
#  V7. Wall +2 / Hedge +1 are HEXSIDE TEM (B9.3x) that apply only when the
#      fire crosses that hexside — never auto-applied here, warning only.
#      Exact sub-rule id (B9.31?) not verified.
#  V8. MG usage limits (a squad firing >1 MG forfeits inherent FP, HS/crew
#      firing a MG forfeits inherent FP — A9.1x): NOT enforced; a warning is
#      emitted when the SW count exceeds the squad count. Rule id unverified.
#  V9. Captured (enemy-nationality) SW fire at penalty (A21.11 ff.): never
#      auto-fired here, warning only; rule id unverified. Note: "(r)"-model
#      weapons drawn in the firer's own nationality art (e.g. Finnish
#      "LMG (r)" = Russian-model DP in Finnish OB) are NOT captured use.
#  V10. Fire INTO/out of a Melee Location is restricted (A7.21 forbids IFT
#      attacks BY units in Melee; restrictions on firing INTO Melee not
#      modeled). Warning only.
#  V11. Hero/SMC inherent FP not modeled (treated as 0-FP, warning).
#  V12. Sangar TEM +1 vs direct fire (F8.4 — read locally but from the
#      chapter F desert rules; double-check applicability).
#  V13. Debris TEM (O1.4 / deluxe) unknown — falls into the unknown-terrain
#      warning path (TEM 0 + warning).
#  V14. PBF level condition: A7.21 doubles FP while "ADJACENT ... and either
#      within one level of or higher than" the target. We check hex base
#      elevation when terrain data is present (deny PBF when the firer is
#      ≥2 levels BELOW the target) but building/upper-level Locations are
#      not modeled.
#  V15. Squad Assault Fire (A7.36) / Spraying Fire (A7.34) capability is read
#      from VASL 6.7.3 counter-art underscore elements (see
#      app/asl/unit_capabilities.py; rule text verified verbatim). Two art
#      oddities are encoded as drawn and flagged there: ge436Ss (SS 4-3-6)
#      and the ge447S_sap variant lack the SprayingFire underscore their
#      base counterparts have.
# ============================================================================
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.asl import ift, unit_capabilities

_VSAV = None


def _geo():
    """Lazy import of vsav_service for the shared board-geometry constants.

    Imported lazily because app.services.__init__ imports asl_service, which
    imports app.asl.tools, which imports this module — a top-level import
    here would be circular.
    """
    global _VSAV
    if _VSAV is None:
        from app.services import vsav_service
        _VSAV = vsav_service
    return _VSAV

# ----------------------------------------------------------------------------
# Rule data
# ----------------------------------------------------------------------------

# Target-hex terrain TEM vs Direct Fire, keyed by the names board_terrain
# emits. Each entry: (TEM, rule id). Names absent here fall back to
# (0, None) with a warning. Hindrance-vs-TEM distinction: grain / brush /
# orchard are LOS Hindrances for INTERVENING hexes only and have NO TEM for
# fire into them (B15.3 / B12.3 / B14.3) — they do negate FFMO/Interdiction.
TERRAIN_TEM: Dict[str, Tuple[int, str]] = {
    "Open Ground": (0, "B0 (TEC)"),
    "Plowed Field": (0, "B15.6"),       # treated as Open Ground out of season
    "Snow": (0, "B0 (TEC)"),
    "Deep Snow": (0, "E3.73"),
    "Dirt Road": (0, "B3"),
    "Paved Road": (0, "B3"),
    "Elevated Road": (0, "B5"),
    "Sunken Road": (0, "B4"),
    "Path": (0, "B3"),
    "Track": (0, "B3"),
    "Runway": (0, "B3"),
    "Woods": (1, "B13.3"),              # +1 vs Direct Fire (Air Bursts: -1 vs Indirect)
    "Pine Woods": (1, "B13.8"),         # treated exactly like woods
    "Forest": (2, "B13.7"),             # TEM +2 rather than +1
    "Light Woods": (1, "B13 (SSR)"),
    "Brush": (0, "B12.3"),              # no TEM; negates FFMO/Interdiction
    "Grain": (0, "B15.3"),
    "Light Grain": (0, "B15.3"),
    "Orchard": (0, "B14.3"),            # no TEM regardless of season
    "Orchard, Out of Season": (0, "B14.3"),
    "Orchard (partial)": (0, "B14.3"),
    "PartialOrchard": (0, "B14.3"),
    "Olive Grove": (1, "B14.8"),
    "Cactus Patch": (1, "B14.7"),
    "Crags": (1, "B17.3"),
    "Marsh": (0, "B16.3"),
    "Shellholes": (1, "B2.3"),          # conditional: Infantry only, must be claimed
    "Graveyard": (1, "B18"),            # VERIFY (V5)
    "Huts": (1, "G5"),                  # VERIFY (V2)
    "Light Jungle": (1, "G2.1"),        # VERIFY (V3)
    "Dense Jungle": (2, "G2.2"),
    "Palm Trees": (0, "G4"),            # VERIFY (V4)
    "Sangar": (1, "F8.4"),              # VERIFY (V12)
    # Buildings: wooden +2 / stone +3 (TEC; A7.x examples & Squad Leader 101
    # Q&A in eASLRB confirm "wooden +2 ... stone building is the best with a
    # +3"). Factories take normal building TEM vs fire from OUTSIDE the
    # factory; fire traced completely within the same factory is +1 (B23.741).
    "Stone Rubble": (3, "B24.3"),       # rubble TEM = TEM of its building type
    "Wooden Rubble": (2, "B24.3"),
}

_STONE_BUILDING_TEM = (3, "B23.71 (TEC)")
_WOODEN_BUILDING_TEM = (2, "B23.71 (TEC)")

# Hexside terrain that may appear in a terrain sample but is never
# auto-applied (TEM only when fire crosses that hexside). VERIFY (V7).
_HEXSIDE_TERRAIN_NOTE = {
    "Wall": "+2 only if the fire crosses the wall hexside (B9.3)",
    "Hedge": "+1 only if the fire crosses the hedge hexside (B9.3)",
    "Bocage": "TEM only across the bocage hexside (B9.5)",
}

# Entrenchments (B27): +2 TEM vs Direct Fire / on-board mortars
# [+4 vs OBA/OVR, not modeled here]. Not cumulative with other positive TEM
# (B27.3), and a unit beneath a foxhole is a separate Location from the rest
# of the hex for TEM purposes (B27.13) — so it REPLACES hex terrain TEM.
# Trenches follow all foxhole rules (B27.5).
ENTRENCHMENT_TEM = 2          # B27.3
ENTRENCHMENT_RULE = "B27.3"
ENTRENCHMENT_REPLACES_RULE = "B27.13"

# CX: "+1 to IFT DR" for any attack a CX unit makes or directs (A4.51).
CX_DRM = 1                    # A4.51

# SW FP / Normal Range, read from VASL 6.7.3 counter art
# (images/<nat>/<nat><weapon>.svg text values), keyed by
# (side name, normalized weapon name). "no_lr" → may not use Long Range
# (A7.22 EXC: ATR).
# Format: (fp, normal_range, no_long_range)
SW_TABLE: Dict[Tuple[str, str], Tuple[int, int, bool]] = {
    ("Finnish", "LMG"): (3, 8, False),
    ("Finnish", "LMG(R)"): (2, 6, False),   # Russian-model DP in Finnish OB
    ("Finnish", "MMG"): (5, 12, False),
    ("Finnish", "MMG(R)"): (4, 10, False),
    ("Finnish", "HMG"): (7, 16, False),
    ("Finnish", "HMG(R)"): (6, 12, False),
    ("Finnish", "ATR"): (1, 12, True),      # 20L Lahti; counter: no Long Range
    ("Russian", "LMG"): (2, 6, False),
    ("Russian", "MMG"): (4, 10, False),
    ("Russian", "HMG"): (6, 12, False),
    ("Russian", "ATR"): (1, 12, True),
    ("German", "LMG"): (3, 8, False),
    ("German", "MMG"): (5, 12, False),
    ("German", "HMG"): (7, 16, False),
    ("German", "ATR"): (1, 12, True),
    ("American", "LMG"): (2, 6, False),
    ("American", "MMG"): (4, 10, False),
    ("American", "HMG"): (6, 12, False),
    ("American", "ATR"): (1, 12, True),
    ("British", "LMG"): (2, 7, False),
    ("British", "MMG"): (4, 12, False),
    ("British", "HMG"): (6, 14, False),
    ("British", "ATR"): (1, 12, True),
    ("Italian", "LMG"): (2, 5, False),
    ("Italian", "MMG"): (4, 10, False),
    ("Italian", "HMG"): (6, 12, False),
    ("Italian", "ATR"): (1, 12, True),
    ("Japanese", "LMG"): (2, 6, False),
    ("Japanese", "MMG"): (4, 11, False),
    ("Japanese", "HMG"): (6, 14, False),
}

# Fallback when the nationality isn't in SW_TABLE. VERIFY (V1).
SW_GENERIC: Dict[str, Tuple[int, int, bool]] = {
    "LMG": (2, 6, False),
    "MMG": (4, 10, False),
    "HMG": (6, 12, False),
    "ATR": (1, 12, True),
}

# A7.9 cowering exemptions we can infer from the save. Finns never cower
# [EXC: Conscripts] per A7.9; the other listed exemptions (SMC, berserk,
# Fanatic, British Elite/1st Line, Fire Lane, IFE, vehicular...) need
# information the save doesn't reliably carry.
COWERING_EXEMPT_SIDES = {"Finnish"}   # A7.9

# ----------------------------------------------------------------------------
# Counter-name parsing
# ----------------------------------------------------------------------------

# "6-4-8 1sq", "4-4-7 1sq", "2-2-8 Icr", "3-4-8 2hs" → FP-range-morale.
_SQUAD_RE = re.compile(r"^(\d+(?:\.5)?)-(\d+)-(\d+)\b(.*)$")
# Concrete leader counters: "9-1", "8-0", "10-2", "6+1".
_LEADER_MOD_RE = re.compile(r"^(\d+)([+-]\d)\b")
# Generic leader/commissar piece names: fiLDR, ruLDR, ruCOM, geLDR...
_GENERIC_LEADER_RE = re.compile(r"^[a-z]{2}(LDR|COM)\b")
_HERO_RE = re.compile(r"\bHERO\b", re.IGNORECASE)
# Ordnance: mortars and Guns resolve on the To-Hit process (C3), not the IFT.
_MORTAR_RE = re.compile(r"\bMTR\b", re.IGNORECASE)
_GUN_RE = re.compile(
    r"\b(AT|ART|INF|RCL|How|Gun|PTP|obr)\b|^\d{2,3}(L{1,2}|\*)?\s", re.IGNORECASE
)
_SW_NAME_RE = re.compile(r"^(LMG|MMG|HMG|ATR)\b", re.IGNORECASE)


def _norm_sw_name(name: str) -> str:
    """'LMG (r)' -> 'LMG(R)'."""
    return re.sub(r"\s+", "", name).upper()


def classify_unit(unit: Dict[str, Any]) -> Dict[str, Any]:
    """Classify one parsed unit entry by its counter name.

    Returns {kind, fp, normal_range, morale, no_long_range, leadership,
    detail} with None for fields that don't apply. kind is one of:
    'personnel' (squad/HS/crew), 'leader', 'sw', 'ordnance', 'hero',
    'unknown'. Personnel additionally get {assault_fire, spraying_fire,
    cap_source, cap_note}: True/False when the counter is in the
    unit_capabilities table (keyed by the unit's `art` path, with a
    nationality+strength fallback), None when unknown.
    """
    name = (unit.get("name") or "").strip()
    out: Dict[str, Any] = dict(kind="unknown", fp=None, normal_range=None,
                               morale=None, no_long_range=False,
                               leadership=None, detail=None)
    if _MORTAR_RE.search(name):
        out.update(kind="ordnance", detail="mortar")
        return out
    m = _SQUAD_RE.match(name)
    if m:
        fp_txt = m.group(1)
        caps = unit_capabilities.lookup(unit.get("art"), unit.get("side"),
                                        name)
        out.update(
            kind="personnel",
            fp=float(fp_txt) if "." in fp_txt else int(fp_txt),
            normal_range=int(m.group(2)),
            morale=int(m.group(3)),
            detail=m.group(4).strip() or None,
            assault_fire=caps["assault_fire"],
            spraying_fire=caps["spraying_fire"],
            cap_source=caps["source"],
            cap_note=caps["note"],
        )
        return out
    m = _LEADER_MOD_RE.match(name)
    if m:
        out.update(kind="leader", morale=int(m.group(1)),
                   leadership=int(m.group(2)), detail=name)
        return out
    if _GENERIC_LEADER_RE.match(name):
        kind_txt = "commissar" if "COM" in name else "leader"
        out.update(kind="leader", detail=f"generic {kind_txt} counter "
                                         "(modifier not recoverable)")
        return out
    if _HERO_RE.search(name):
        out.update(kind="hero", detail="hero (inherent FP not modeled)")  # VERIFY (V11)
        return out
    sw_name = _norm_sw_name(name)
    msw = _SW_NAME_RE.match(sw_name)
    if msw:
        side = unit.get("side")
        entry = SW_TABLE.get((side, sw_name))
        generic = False
        if entry is None:
            entry = SW_GENERIC.get(msw.group(1).upper())
            generic = entry is not None
        if entry:
            fp, rng, no_lr = entry
            out.update(kind="sw", fp=fp, normal_range=rng, no_long_range=no_lr,
                       detail="generic SW values (nationality not in table)"
                              if generic else None)
            return out
    if _GUN_RE.search(name):
        out.update(kind="ordnance", detail="Gun")
        return out
    return out


# ----------------------------------------------------------------------------
# Hex geometry
# ----------------------------------------------------------------------------

_HEX_LABEL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _axial(label: str) -> Tuple[int, int]:
    """Hex label -> (column index, doubled-height row) on a geo board."""
    letters = _geo().LETTERS
    m = _HEX_LABEL_RE.match(label.upper())
    if not m or m.group(1) not in letters:
        raise ValueError(f"Bad hex label: {label!r}")
    i = letters.index(m.group(1))
    r = int(m.group(2))
    # Matches vsav_service geometry: odd columns (B, D, ...) have rows 0..10
    # at y = r*DY; even columns rows 1..10 at y = DY/2 + (r-1)*DY.
    yh = 2 * r if i % 2 == 1 else 2 * r - 1
    return i, yh


def hex_range_same_board(a: str, b: str) -> int:
    """Exact hex range between two labels on the same geo board."""
    i1, y1 = _axial(a)
    i2, y2 = _axial(b)
    dcol = abs(i1 - i2)
    dyh = abs(y1 - y2)
    return dcol + max(0, (dyh - dcol) // 2)


def _board_box(board: Dict[str, Any]) -> Dict[str, float]:
    g = _geo()
    crop = board.get("crop") or {}
    cw = crop.get("w", -1)
    ch = crop.get("h", -1)
    disp_w = cw if cw and cw > 0 else g.BOARD_W
    disp_h = ch if ch and ch > 0 else g.BOARD_H
    c, r = board["slot"]
    return dict(x0=g.EDGE + c * disp_w, y0=g.EDGE + r * disp_h,
                w=disp_w, h=disp_h,
                cx=crop.get("x", 0) or 0, cy=crop.get("y", 0) or 0)


def _hex_center_map_px(board: Dict[str, Any], label: str) -> Tuple[float, float]:
    """Hex label on a (possibly reversed/cropped) board -> map pixel center."""
    g = _geo()
    i, _ = _axial(label)
    r = int(_HEX_LABEL_RE.match(label.upper()).group(2))
    xo = i * g.DX
    yo = r * g.DY if i % 2 == 1 else g.DY / 2 + (r - 1) * g.DY
    box = _board_box(board)
    if board.get("reversed"):
        lx = box["cx"] + box["w"] - xo
        ly = box["cy"] + box["h"] - yo
    else:
        lx = xo - box["cx"]
        ly = yo - box["cy"]
    return box["x0"] + lx, box["y0"] + ly


def hex_range_cross_board(state: Dict[str, Any], base_a: str, label_a: str,
                          base_b: str, label_b: str) -> Optional[int]:
    """Best-effort hex range across adjoining boards via map-pixel geometry."""
    boards = {b.get("base") or b["name"].lstrip("r"): b
              for b in state.get("boards", [])}
    ba, bb = boards.get(base_a), boards.get(base_b)
    if ba is None or bb is None:
        return None
    g = _geo()
    xa, ya = _hex_center_map_px(ba, label_a)
    xb, yb = _hex_center_map_px(bb, label_b)
    dcol = int(round(abs(xa - xb) / g.DX))
    dyh = int(round(abs(ya - yb) / (g.DY / 2)))
    return dcol + max(0, (dyh - dcol) // 2)


# ----------------------------------------------------------------------------
# Hex lookup
# ----------------------------------------------------------------------------

def _find_hex_key(state: Dict[str, Any], hex_id: str) -> str:
    """Resolve a user/model-supplied hex id to a state['hexes'] key."""
    hexes = state.get("hexes", {})
    hid = hex_id.strip()
    if hid in hexes:
        return hid
    for k in hexes:
        if k.lower() == hid.lower():
            return k
    if "-" not in hid:
        matches = [k for k in hexes if k.split("-", 1)[-1].upper() == hid.upper()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"Hex {hex_id!r} is ambiguous across boards: {sorted(matches)}. "
                "Use the full '<board>-<hex>' id from the BOARD STATE block."
            )
    raise ValueError(
        f"Hex {hex_id!r} has no units in the parsed save. Use a "
        "'<board>-<hex>' id listed in the BOARD STATE block (e.g. '57-H9')."
    )


def _split_hex_key(key: str) -> Tuple[str, str]:
    base, _, label = key.partition("-")
    return base, label.upper()


def _all_hex_markers(entry: Dict[str, Any]) -> set:
    """Every marker present in the hex: hex-level (unattributed) markers
    plus markers/entrenchments attributed to individual units by stack
    order (parse_vsav puts a marker only on the units beneath it)."""
    mk = set(entry.get("markers") or [])
    for u in entry.get("units", []):
        mk.update(u.get("markers") or [])
        if u.get("entrenched_by"):
            mk.add(u["entrenched_by"])
    return mk


def ski_state(u: Dict[str, Any]) -> Optional[str]:
    """'worn' | 'carried' | None for one parsed unit.

    parse_vsav decodes the VASL ski counter's face into a per-unit `skis`
    field: "worn" = the "Skis" face is up, the unit is a Skier in ski mode
    (E4.2: "Units on skis are in ski mode and are referred to as Skiers.
    Skiers are identified by placing the possessed ski counter with the
    'Skis' up."); "carried" = the "OFF Skis" face is up (E4.21: "When not
    in ski mode, skis are carried atop a unit with the 'OFF Skis' side up
    at a cost of one PP."). Fallback for hand-built state dicts / a marker
    whose face could not be decoded: a bare "Skis" marker counts as worn
    (the counter's base face).
    """
    s = u.get("skis")
    if s in ("worn", "carried"):
        return s
    return "worn" if "Skis" in (u.get("markers") or []) else None


def _unit_entrenchment(u: Dict[str, Any]) -> Optional[str]:
    """'Foxhole'/'Trench' if THIS unit is in one, else None.

    parse_vsav sets `entrenched_by` from stack order (the unit is below the
    entrenchment counter). The markers-list fallback keeps hand-built state
    dicts (tests, future callers) working.
    """
    ent = u.get("entrenched_by")
    if ent:
        return ent
    markers = u.get("markers") or []
    for name in ("Trench", "Foxhole"):
        if name in markers:
            return name
    return None


# ----------------------------------------------------------------------------
# TEM derivation
# ----------------------------------------------------------------------------

def _terrain_tem(hex_entry: Dict[str, Any], warnings: List[str],
                 assumptions: List[str]) -> Tuple[int, str]:
    """(TEM, label) for the target hex's terrain (no entrenchment)."""
    terr = hex_entry.get("terrain")
    if not terr:
        warnings.append(
            "No terrain data for the target hex (board archive unavailable) — "
            "terrain TEM assumed 0; verify manually."
        )
        return 0, "terrain unknown — assumed Open Ground (TEM 0)"
    best = (0, "B0 (TEC)", "Open Ground")
    for part in terr.get("parts", []):
        if part in _HEXSIDE_TERRAIN_NOTE:
            warnings.append(
                f"{part} present in the target hex: hexside TEM applies "
                f"{_HEXSIDE_TERRAIN_NOTE[part]}; NOT auto-applied."
            )
            continue
        if "Building" in part or "Factory" in part or "Rowhouse" in part \
                or "Market" in part:
            tem, rule = (_STONE_BUILDING_TEM if "Stone" in part
                         else _WOODEN_BUILDING_TEM)
            if "Factory" in part:
                assumptions.append(
                    "Factory target: normal building TEM applied — fire traced "
                    "completely within the same factory would be +1 instead "
                    "(B23.741)."
                )
            if "Market" in part:
                warnings.append(
                    "Marketplace hex treated as its building type; B23.733 "
                    "exceptions not modeled."  # VERIFY (V6)
                )
        elif part in TERRAIN_TEM:
            tem, rule = TERRAIN_TEM[part]
            if part == "Shellholes" and tem:
                assumptions.append(
                    "Shellhole TEM is conditional: Infantry only, and only if "
                    "claimed on entry (B2.3); assumed claimed."
                )
        else:
            warnings.append(
                f"Unknown terrain {part!r} in target hex — TEM assumed 0; "
                "verify manually."
            )
            tem, rule = 0, None
        if tem > best[0]:
            best = (tem, rule, part)
    tem, rule, part = best
    label = f"{part} TEM ({rule})" if rule else f"{part} (unverified, TEM 0)"
    return tem, label


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

VALID_PHASES = ("prep", "advancing", "defensive_first", "defensive_final")


def resolve_attack(
    state: Dict[str, Any],
    firing_hex: str,
    target_hex: str,
    phase: str = "prep",
    firing_unit_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a fire attack between two hexes of a parsed .vsav state.

    Returns an itemized, auditable derivation (firers, fp/drm breakdowns,
    assumptions, warnings) with the full `ift.compute_attack` output embedded
    under "ift". Raises ValueError for unusable inputs (bad hex ids, no
    eligible firers, ...). Never mutates `state`.
    """
    if phase not in VALID_PHASES:
        raise ValueError(f"Invalid phase {phase!r}. Must be one of {VALID_PHASES}.")
    if not isinstance(state, dict) or "hexes" not in state:
        raise ValueError("state does not look like a parse_vsav() result.")

    warnings: List[str] = []
    assumptions: List[str] = [
        "LOS from the firing hex to the target hex is assumed CLEAR — there "
        "is no LOS engine; obstacles/blind hexes are NOT checked (A6).",
        "LOS Hindrances in INTERVENING hexes (grain, brush, in-season "
        "orchard, SMOKE, wrecks: +1 each, B.6/A6.7) are NOT included — add "
        "them to the DRM manually if present along the path.",
        "Map overlays are NOT applied to terrain.",
        "Entrenchment containment is read from VASL stack order: a unit "
        "BELOW a Foxhole/Trench counter in its stack is IN it; a unit above "
        "the counter (or not in its stack) is NOT (B27).",
        "Same-level fire assumed except where hex base elevation says "
        "otherwise; building upper levels are not modeled.",
        "Fire group = all eligible units in the single firing hex; "
        "multi-hex Fire Groups (A7.55) are not supported.",
    ]

    fkey = _find_hex_key(state, firing_hex)
    tkey = _find_hex_key(state, target_hex)
    if fkey == tkey:
        warnings.append(
            "Firing hex equals target hex: IFT attacks within the same "
            "Location are rarely legal (A7.21/A7.211) — treated as TPBF."
        )
    fentry = state["hexes"][fkey]
    tentry = state["hexes"][tkey]

    # ---- range ----
    fbase, flabel = _split_hex_key(fkey)
    tbase, tlabel = _split_hex_key(tkey)
    if fbase == tbase:
        rng = hex_range_same_board(flabel, tlabel)
        range_method = "same-board hex geometry (exact)"
    else:
        rng = hex_range_cross_board(state, fbase, flabel, tbase, tlabel)
        range_method = "cross-board map geometry (best effort)"
        warnings.append(
            "Firing and target hexes are on different boards — range computed "
            "from map-pixel geometry; verify on the map."
        )
        if rng is None:
            raise ValueError(
                f"Could not compute cross-board range {fkey} -> {tkey} "
                "(board metadata missing)."
            )

    # ---- firing side ----
    units = [u for u in fentry.get("units", [])]
    side_votes: Dict[str, int] = {}
    for u in units:
        cls = classify_unit(u)
        if cls["kind"] in ("personnel", "leader", "hero") and u.get("side") \
                and not u.get("broken"):
            side_votes[u["side"]] = side_votes.get(u["side"], 0) + 1
    if not side_votes:
        for u in units:
            if u.get("side") and not u.get("broken"):
                side_votes[u["side"]] = side_votes.get(u["side"], 0) + 1
    if not side_votes:
        raise ValueError(f"No usable units in firing hex {fkey}.")
    firing_side = max(side_votes, key=side_votes.get)
    if len(side_votes) > 1:
        warnings.append(
            f"Firing hex {fkey} contains units of multiple sides "
            f"({', '.join(sorted(side_votes))}); firing side inferred as "
            f"{firing_side} (most Good Order personnel). Note the Melee rules "
            "if these units are locked in CC."  # VERIFY (V10)
        )
    if "Melee" in _all_hex_markers(fentry):
        warnings.append(
            "Firing hex is marked Melee: IFT attacks are not allowed by units "
            "in Melee (A7.21) — this resolution is hypothetical."
        )
    if "Melee" in _all_hex_markers(tentry):
        warnings.append(
            "Target hex is marked Melee: firing into a Melee Location is "
            "restricted and risks hitting friendly units; not modeled."  # VERIFY (V10)
        )

    # ---- firer eligibility ----
    filt = (firing_unit_filter or "").strip().lower()
    firers: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    leaders: List[Dict[str, Any]] = []
    skis_worn = False
    skis_carried = False

    def _exclude(u, reason):
        excluded.append({"name": u.get("name"), "side": u.get("side"),
                         "reason": reason})

    personnel: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    sws: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for u in units:
        cls = classify_unit(u)
        markers = u.get("markers") or []
        st = ski_state(u)
        if st == "worn":
            skis_worn = True
        elif st == "carried":
            skis_carried = True
        if filt and filt not in (u.get("name") or "").lower() \
                and cls["kind"] != "leader":
            _exclude(u, f"excluded by firing_unit_filter {firing_unit_filter!r}")
            continue
        if u.get("side") and u["side"] != firing_side:
            if cls["kind"] in ("sw", "ordnance"):
                _exclude(u, f"enemy ({u['side']}) weapon — abandoned/captured; "
                            "not fired (captured use would be penalized, "
                            "A21.1x)")  # VERIFY (V9)
            else:
                _exclude(u, f"enemy unit ({u['side']}) — not part of the "
                            f"{firing_side} attack")
            continue
        if u.get("broken"):
            _exclude(u, "BROKEN — broken units may not attack (A10.5x)")
            continue
        if cls["kind"] == "ordnance":
            _exclude(u, f"ordnance ({cls['detail']}) — resolves on the To-Hit "
                        "process (C3), NOT the IFT; excluded from this attack")
            continue
        if phase == "prep" and "Prep Fire" in markers:
            _exclude(u, "already marked Prep Fire — has fired this PFPh")
            continue
        if phase in ("defensive_first", "defensive_final") and \
                ("First Fire" in markers or "Final Fire" in markers):
            warnings.append(
                f"{u.get('name')} is already marked "
                f"{'Final' if 'Final Fire' in markers else 'First'} Fire — "
                "Subsequent First Fire / FPF restrictions (A8.3, A8.31) apply "
                "and are not modeled."
            )
        if cls["kind"] == "leader":
            leaders.append({"unit": u, "cls": cls})
            continue
        if cls["kind"] == "hero":
            _exclude(u, "hero/SMC inherent FP not modeled")  # VERIFY (V11)
            continue
        if cls["kind"] == "unknown":
            _exclude(u, "unrecognized counter name — could not derive FP")
            continue
        if cls["kind"] == "personnel":
            personnel.append((u, cls))
        elif cls["kind"] == "sw":
            sws.append((u, cls))

    if not personnel and not sws:
        raise ValueError(
            f"No eligible {firing_side} firers in {fkey} "
            f"(see exclusions: {[e['reason'] for e in excluded]})."
        )
    if sws and not personnel:
        raise ValueError(
            f"SW in {fkey} have no Good Order {firing_side} manning unit — "
            "no valid attack."
        )

    n_squads = sum(1 for _, c in personnel if c["detail"] and "sq" in c["detail"])
    if len(sws) > max(n_squads, 1):
        warnings.append(
            "More MG/SW than squads in the firing stack: MG usage limits "
            "(firing extra MG forfeits inherent FP, A9.1x) are NOT enforced — "
            "totals may be optimistic."  # VERIFY (V8)
        )

    # ---- PBF / long range per unit ----
    felev = (fentry.get("terrain") or {}).get("elevation")
    telev = (tentry.get("terrain") or {}).get("elevation")
    pbf_mode = "none"
    if rng == 0:
        pbf_mode = "tpbf"
    elif rng == 1:
        pbf_mode = "pbf"
        if felev is not None and telev is not None and telev - felev >= 2:
            pbf_mode = "none"  # VERIFY (V14)
            warnings.append(
                "PBF denied: firer appears ≥2 levels below the target "
                "(A7.21 requires within one level of or higher than the "
                "target)."
            )

    all_pinned = personnel and all("Pin" in (u.get("markers") or [])
                                   for u, _ in personnel)
    cx_present = any("CX" in (u.get("markers") or [])
                     for u, _ in personnel + sws)
    encircled_firer = any("Encircled" in (u.get("markers") or [])
                          for u, _ in personnel + sws)

    ift_units: List[Dict[str, Any]] = []
    firer_rows: List[Dict[str, Any]] = []

    def _add_firer(u, cls, kind_label):
        markers = u.get("markers") or []
        notes: List[str] = []
        if cls.get("detail") and cls["kind"] == "sw":
            notes.append(cls["detail"])  # generic-values note, VERIFY (V1)
        pinned = "Pin" in markers if cls["kind"] == "personnel" else all_pinned
        if pinned and cls["kind"] == "sw":
            notes.append("manning unit pinned — MG fires as Area Fire "
                         "(A7.81); applied as a halving")
        long_range = False
        nr = cls["normal_range"]
        if nr is not None and rng > nr:
            if cls.get("no_long_range") or rng > 2 * nr:
                _exclude(u, f"target at range {rng} exceeds "
                            f"{'Normal Range (no Long Range use)' if cls.get('no_long_range') else 'double Normal Range'}"
                            f" of {nr} (A7.22)")
                return
            long_range = True
            notes.append(f"Long Range (range {rng} > Normal Range {nr}): "
                         "FP halved (A7.22)")
        if u.get("concealed_by"):
            notes.append("firer is concealed — firing forfeits concealment "
                         "(A12.41); no FP effect")
        # A7.36 Assault Fire (verified verbatim, eASLRB v3.14): "Assault Fire
        # capability allows any squad using its inherent FP during the AFPh
        # to add one FP to its Small Arms Fire attack after all modification
        # to the squad's inherent FP; any fraction in its FP is then rounded
        # up. The Assault Fire bonus is not applicable to Opportunity Fire or
        # Long Range Fire, but is still applicable to pinned-firers/
        # Spraying-Fire in the AFPh." Capability = the underscored FP factor,
        # read from the counter art (unit_capabilities). The ift engine
        # encodes the effect (incl. the Long Range NA); squads only — SW and
        # HS/crew counters carry no underscore.
        assault_fire = False
        if cls["kind"] == "personnel":
            af = cls.get("assault_fire")
            if phase == "advancing":
                if af:
                    assault_fire = True
                    notes.append(
                        "Assault Fire: +1 FP after all other modification, "
                        "fractions rounded up (A7.36) — underscored FP, "
                        f"{cls.get('cap_source')}")
                elif af is None:
                    warnings.append(
                        f"{u.get('name')}: Assault Fire capability UNKNOWN "
                        "(counter not in the capability table) — the A7.36 "
                        "+1 is NOT applied. If its FP factor is underscored, "
                        "re-run via ift_attack with assault_fire set."
                    )
                if cls.get("cap_note"):
                    notes.append(cls["cap_note"])
            if cls.get("spraying_fire") and rng <= 3:
                notes.append(
                    "capable of Spraying Fire (underscored Range, A7.34): "
                    "could instead attack two adjacent Locations within 3 "
                    "hexes as Area Fire — NOT applied here (requires a "
                    "two-Location target choice)")
        entry = {"fp": cls["fp"], "pbf": pbf_mode, "long_range": long_range,
                 "pinned": bool(pinned), "assault_fire": assault_fire}
        ift_units.append(entry)
        firer_rows.append({
            "name": u.get("name"), "kind": kind_label, "fp": cls["fp"],
            "normal_range": nr, "morale": cls["morale"],
            "pinned": bool(pinned),
            "assault_fire": cls.get("assault_fire"),
            "spraying_fire": cls.get("spraying_fire"),
            "markers": markers, "notes": notes,
        })

    for u, cls in personnel:
        _add_firer(u, cls, "personnel")
    for u, cls in sws:
        _add_firer(u, cls, "sw")

    if not ift_units:
        raise ValueError(
            f"All candidate firers in {fkey} were excluded "
            f"({[e['reason'] for e in excluded]})."
        )

    # ---- leadership ----
    leadership = 0
    leader_directs = False
    for l in leaders:
        u, cls = l["unit"], l["cls"]
        if "Pin" in (u.get("markers") or []):
            warnings.append(
                f"Leader {u.get('name')} is pinned — direction not applied "
                "(a pinned leader's direction is degraded; not modeled)."
            )
            continue
        if cls["leadership"] is None:
            warnings.append(
                f"Leader {u.get('name')} is a generic counter — leadership "
                "DRM not recoverable from the save; omitted. If he directs "
                "the attack, apply his printed DRM and note that ANY "
                "direction also prevents cowering (A7.9, A7.531)."
            )
            continue
        if cls["leadership"] < leadership or not leader_directs:
            leadership = min(leadership, cls["leadership"]) \
                if leader_directs else cls["leadership"]
            leader_directs = True
    if leader_directs and leadership > 0:
        # A positive-DRM leader would not normally be chosen to direct.
        warnings.append(
            f"Only leader available has a +{leadership} modifier — direction "
            "is optional, so he is assumed NOT to direct; cowering applies "
            "normally (A7.531)."
        )
        leadership = 0
        leader_directs = False

    # ---- target ----
    t_units = tentry.get("units", [])
    if not t_units:
        raise ValueError(f"Target hex {tkey} contains no units in the save.")
    if all(u.get("side") == firing_side for u in t_units if u.get("side")):
        warnings.append(
            f"Target hex {tkey} contains only {firing_side} (friendly) units."
        )

    target_personnel: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for u in t_units:
        cls = classify_unit(u)
        if cls["kind"] == "personnel":
            target_personnel.append((u, cls))
        elif cls["kind"] == "leader" and cls["leadership"] is None:
            warnings.append(
                f"Leader {u.get('name')} in the target hex: morale/leadership "
                "unknown from the counter name — MC odds below ignore any "
                "leader MC DRM (A10.7)."
            )
        if u.get("broken"):
            warnings.append(
                f"Target unit {u.get('name')} is BROKEN: further MC failures "
                "cause Casualty Reduction (A10.31); DM/broken status does NOT "
                "change the IFT DR itself."
            )
        if "DM" in (u.get("markers") or []):
            warnings.append(
                "DM on the target affects rally, not this IFT DR."
            )
        if "Pin" in (u.get("markers") or []):
            warnings.append(
                "Target is pinned: no change to the IFT DR (pin affects the "
                "target's own actions, A7.8)."
            )

    # Concealment: attacks vs a concealed target are Area Fire (A7.23).
    area_fire_halvings = 0
    conc = [bool(u.get("concealed_by")) for u in t_units]
    if conc and all(conc):
        area_fire_halvings = 1
        warnings.append(
            "All target units are concealed — FP halved as Area Fire (A7.23)."
        )
    elif any(conc):
        warnings.append(
            "Target hex mixes concealed and unconcealed units: attacks vs "
            "the concealed ones are halved (A7.23); odds below are computed "
            "WITHOUT the halving (vs the unconcealed units)."
        )

    # ---- DRM ledger ----
    # Per-unit entrenchment is read from VASL stack order (parse_vsav sets
    # `entrenched_by` on each unit BELOW a Foxhole/Trench counter). If the
    # target units split between in/out of the entrenchment, the attack is
    # resolved once per distinct final DRM (same FP column, different TEM).
    drm_breakdown: List[Dict[str, Any]] = []
    t_markers = _all_hex_markers(tentry)
    in_ent = [u for u in t_units if _unit_entrenchment(u)]
    out_ent = [u for u in t_units if not _unit_entrenchment(u)]
    ent_kind = ("Trench" if any(_unit_entrenchment(u) == "Trench"
                                for u in in_ent) else "Foxhole")

    terrain_tem_value, terrain_tem_label = _terrain_tem(
        tentry, warnings, assumptions)

    entrenched: Any = bool(in_ent) and not out_ent
    mixed = bool(in_ent) and bool(out_ent)
    if mixed:
        entrenched = "mixed"

    # tem_groups: one entry per distinct TEM the target units take; each is
    # resolved separately on the IFT below.
    if entrenched is True:
        tem_groups = [{"tem": ENTRENCHMENT_TEM, "units": t_units,
                       "desc": f"all target units IN the {ent_kind}"}]
        drm_breakdown.append({
            "label": (f"{ent_kind} TEM +{ENTRENCHMENT_TEM} "
                      f"({ENTRENCHMENT_RULE}; entrenchment TEM REPLACES hex "
                      f"terrain TEM — not cumulative, the occupant is a "
                      f"separate Location for TEM, {ENTRENCHMENT_REPLACES_RULE})"),
            "drm": ENTRENCHMENT_TEM,
        })
        if terrain_tem_value > ENTRENCHMENT_TEM:
            warnings.append(
                f"Hex terrain TEM ({terrain_tem_label}, "
                f"+{terrain_tem_value}) exceeds the entrenchment TEM; units "
                "NOT beneath the entrenchment would claim the terrain TEM "
                "instead (B27.13)."
            )
        elif terrain_tem_value:
            drm_breakdown.append({
                "label": f"[replaced] {terrain_tem_label} "
                         f"+{terrain_tem_value} — superseded by the "
                         "entrenchment TEM (B27.3)",
                "drm": 0,
            })
    elif mixed:
        in_names = ", ".join(u.get("name", "?") for u in in_ent)
        out_names = ", ".join(u.get("name", "?") for u in out_ent)
        drm_breakdown.append({
            "label": (f"IN {ent_kind} ({in_names}): TEM "
                      f"+{ENTRENCHMENT_TEM} ({ENTRENCHMENT_RULE}; replaces "
                      f"hex terrain TEM, {ENTRENCHMENT_REPLACES_RULE})"),
            "drm": ENTRENCHMENT_TEM,
        })
        drm_breakdown.append({
            "label": (f"NOT in {ent_kind} ({out_names}): "
                      f"{terrain_tem_label} +{terrain_tem_value}"),
            "drm": terrain_tem_value,
        })
        if terrain_tem_value == ENTRENCHMENT_TEM:
            tem_groups = [{"tem": ENTRENCHMENT_TEM, "units": t_units,
                           "desc": "all target units (entrenchment and "
                                   "terrain TEM happen to be equal)"}]
            warnings.append(
                f"MIXED occupancy in target hex {tkey} (stack order): "
                f"{in_names} IN the {ent_kind}, {out_names} NOT — but both "
                f"TEMs equal +{ENTRENCHMENT_TEM}, so a single resolution "
                "covers everyone."
            )
        else:
            tem_groups = [
                {"tem": ENTRENCHMENT_TEM, "units": in_ent,
                 "desc": f"units IN the {ent_kind} ({in_names})"},
                {"tem": terrain_tem_value, "units": out_ent,
                 "desc": f"units NOT in the {ent_kind} ({out_names})"},
            ]
            warnings.append(
                f"MIXED TEM in target hex {tkey}: stack order shows "
                f"{in_names} IN the {ent_kind} (+{ENTRENCHMENT_TEM}, "
                f"{ENTRENCHMENT_RULE}) but {out_names} NOT in it "
                f"(+{terrain_tem_value}, hex terrain) — the attack is "
                "resolved once per TEM; see the per-group resolutions."
            )
    else:
        tem_groups = [{"tem": terrain_tem_value, "units": t_units,
                       "desc": "all target units (no entrenchment)"}]
        drm_breakdown.append({
            "label": f"TEM: {terrain_tem_label}",
            "drm": terrain_tem_value,
        })

    other_drm: List[Dict[str, Any]] = []
    if cx_present:
        other_drm.append({"label": "CX firer: +1 to the IFT DR (A4.51)",
                          "drm": CX_DRM})
        drm_breakdown.append({"label": "CX firer (A4.51)", "drm": CX_DRM})
    if leader_directs and leadership:
        drm_breakdown.append({
            "label": f"leadership direction {leadership:+d} (A7.531; also "
                     "prevents cowering, A7.9)",
            "drm": leadership,
        })
    if encircled_firer:
        drm_breakdown.append({"label": "encircled firer +1 (A7.7)", "drm": 1})

    # FFMO/FFNAM are Defensive First Fire DRM vs MOVING units (A4.6) — never
    # applicable to Prep/Advancing Fire, and the save records no movement.
    if phase in ("prep", "advancing"):
        assumptions.append(
            "FFMO/FFNAM not applicable: they exist only in Defensive First "
            "Fire vs moving units (A4.6) — never in "
            f"{'Prep' if phase == 'prep' else 'Advancing'} Fire."
        )
    else:
        warnings.append(
            "Defensive fire phase: FFNAM (-1) / FFMO (-1) depend on the "
            "target's movement, which the save does not record — NOT applied. "
            "Add them via ift_attack if the target was moving (A4.6; FFMO is "
            "negated by any hindrance or positive TEM)."
        )

    if "Encircled" in t_markers:
        warnings.append("Target is Encircled: morale lowered by 1 (A7.7) — "
                        "applied to the MC odds below.")

    # ---- cowering & target morale ----
    firer_cowering_exempt = firing_side in COWERING_EXEMPT_SIDES
    if firer_cowering_exempt:
        assumptions.append(
            f"{firing_side} units do not cower (A7.9) [EXC: Conscripts — "
            "assumed none in the stack]."
        )

    def _group_target(group_units):
        """(ift target spec, morale-source note) for one TEM group."""
        pers = [(u, cls) for u, cls in target_personnel if u in group_units]
        if not pers:
            return None, None
        morales = sorted((cls["morale"] for _, cls in pers), reverse=True)
        morale = morales[0]
        names = ", ".join(u.get("name", "?") for u, _ in pers)
        if len(set(morales)) > 1:
            warnings.append(
                f"Target units ({names}) have mixed morale {morales}; MC "
                f"odds computed vs the highest ({morale}) — each unit "
                "actually checks against its own morale."
            )
        spec = {
            "kind": "personnel",
            "morale": morale,
            "encircled": "Encircled" in t_markers,
        }
        return spec, (
            f"Target morale {morale} taken from the printed counter "
            f"value(s) of: {names}."
        )

    if not target_personnel:
        warnings.append(
            "No personnel counter with a parseable morale in the target hex — "
            "break/pin odds omitted (use ift_attack with an explicit target)."
        )

    if skis_worn:
        # E4.2: a unit with its ski counter "Skis" face up is a Skier (ski
        # mode). E4.6 (verified verbatim): "A Skier may not fire any Gun,
        # ordnance SW, or MMG/HMG; he must change to foot mode first."
        warnings.append(
            "Firing unit(s) have skis WORN (ski mode, E4.2 — they are "
            "Skiers): E4.6 forbids a Skier from firing any Gun, ordnance "
            "SW, or MMG/HMG (he must change to foot mode first) — NOT "
            "enforced here; other Chapter E4 Skier effects are not modeled."
        )
    if skis_carried:
        assumptions.append(
            "Ski counter(s) with the 'OFF Skis' face up in the firing hex: "
            "the skis are merely carried at a cost of one PP (E4.21) — the "
            "units are normal Infantry for this attack, no E4 Skier "
            "effects apply (the 1 PP portage load is not modeled)."
        )

    # ---- run the IFT engine (once per distinct target TEM) ----
    resolutions: List[Dict[str, Any]] = []
    for grp in tem_groups:
        target, morale_note = _group_target(grp["units"])
        if morale_note:
            assumptions.append(morale_note)
        res = ift.compute_attack(
            units=ift_units,
            afph=(phase == "advancing"),
            area_fire_halvings=area_fire_halvings,
            tem=grp["tem"],
            leadership=leadership if leader_directs else 0,
            encircled_firer=encircled_firer,
            other_drm=other_drm,
            firer_cowering_exempt=firer_cowering_exempt,
            target=target,
        )
        res.pop("cells", None)  # UI-only heatmap
        resolutions.append({
            "applies_to": grp["desc"],
            "tem": grp["tem"],
            "drm": res.get("drm"),
            "morale_used": (target or {}).get("morale"),
            "ift": res,
        })
    # FP, column, and cowering are TEM-independent — identical across groups.
    result = resolutions[0]["ift"]
    single = len(resolutions) == 1

    # Attach the per-unit FP audit steps to the named firers.
    for row, fb in zip(firer_rows, result.get("fp_breakdown", [])):
        row["fp_resolution"] = fb

    if phase == "advancing":
        assumptions.append("Advancing Fire: all FP halved (A7.24).")
        applied = [r["name"] for r, e in zip(firer_rows, ift_units)
                   if e.get("assault_fire")]
        if applied:
            assumptions.append(
                "Assault Fire +1 applied (A7.36) for: " + ", ".join(applied)
                + " — capability read deterministically from the counter's "
                "underscored FP factor (VASL counter art / Chapter A "
                "national notes), per A1.21."
            )

    pbf_note = None
    if pbf_mode == "pbf":
        pbf_note = ("Point Blank Fire: FP doubled — firer ADJACENT to the "
                    "target (A7.21)")
    elif pbf_mode == "tpbf":
        pbf_note = ("Triple Point Blank Fire: FP tripled — same Location "
                    "(A7.211)")

    out = {
        "firing_hex": fkey,
        "target_hex": tkey,
        "phase": phase,
        "firing_side": firing_side,
        "range": {
            "hexes": rng,
            "method": range_method,
            "pbf": pbf_mode,
            "note": pbf_note,
        },
        "firers": firer_rows,
        "excluded": excluded,
        "total_fp": result.get("total_fp"),
        "column": result.get("column"),
        "drm_breakdown": drm_breakdown,
        # Mixed-TEM target: there is no single DRM — see "resolutions".
        "drm": result.get("drm") if single else None,
        "cowering": result.get("cowering"),
        "target": {
            "units": [
                {"name": u.get("name"), "side": u.get("side"),
                 "markers": u.get("markers") or [],
                 "entrenched_by": _unit_entrenchment(u),
                 "broken": bool(u.get("broken")),
                 "concealed": bool(u.get("concealed_by"))}
                for u in t_units
            ],
            "terrain": (tentry.get("terrain") or {}).get("terrain"),
            # True / False / "mixed" (per-unit detail in units[].entrenched_by)
            "entrenched": entrenched,
            "morale_used": resolutions[0]["morale_used"] if single else None,
        },
        "warnings": warnings + result.get("warnings", []),
        "assumptions": assumptions,
        "ift": result if single else None,
    }
    if not single:
        out["resolutions"] = resolutions
    logging.info(
        "🎯 resolve_attack(%s -> %s, %s): %s FP col %s, DRM %+d, range %d (%s)",
        fkey, tkey, phase, result.get("total_fp"), result.get("column"),
        result.get("drm") or 0, rng, pbf_mode,
    )
    return out
