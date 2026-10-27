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
#  V7. Wall +2 / Hedge +1 (Bocage as wall, B9.5) are HEXSIDE TEM (B9.3)
#      applied only when the straight LOS enters the TARGET hex across that
#      hexside — detected by tracing the LOS on the board's LOSData grid
#      (board_terrain.los_hexside_crossings). Not cumulative with positive
#      in-hex TEM (B9.31, higher applies). Wall Advantage (B9.32) and the
#      road/gap exception (B9.3) are noted, not modeled. Cactus Hedge: note
#      only. Same-level LOS across a wall/hedge hexside of NEITHER hex is a
#      Half-Level obstacle (B9.2) — warned, not enforced.
#  V8. MG usage limits (a squad firing >1 MG forfeits inherent FP): NOT
#      enforced; a warning is emitted when the SW count exceeds the squad
#      count. HS/crew firing a SW forfeits inherent FP and IS enforced.
#      Rule id unverified.
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
from typing import Any, Dict, List, Optional, Set, Tuple

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

# Hexside TEM when the LOS enters the target hex across that hexside
# (B9.3: wall +2, hedge +1; B9.5: bocage follows the wall rules).
_HEXSIDE_TEM: Dict[str, Tuple[int, str]] = {
    "Wall": (2, "B9.3"),
    "Hedge": (1, "B9.3"),
    "Bocage": (2, "B9.5"),
}
_LOS_CLEAR_ASSUMPTION = (
    "LOS from the firing hex to the target hex is assumed CLEAR — there "
    "is no LOS engine; obstacles/blind hexes are NOT checked (A6)."
)
_LOS_CLEAR_EXCEPT_HEXSIDES_ASSUMPTION = (
    "LOS from the firing hex to the target hex is assumed CLEAR of terrain "
    "obstacles — there is no full LOS engine; blind hexes/obstacles are "
    "NOT checked (A6) — EXCEPT wall/hedge/bocage hexsides, which ARE traced "
    "along the straight LOS on the board's LOS grid (B9) and reported "
    "under los_hexsides."
)


def _board_transforms(state: Dict[str, Any], base: str) -> tuple:
    for b in state.get("boards", []) or []:
        bbase = b.get("base") or str(b.get("name", "")).lstrip("r")
        if str(bbase) == str(base):
            return tuple(b.get("ssr_transforms") or ())
    return ()


def _blocked_los_detail(state: Dict[str, Any], fkey: str, tkey: str,
                        fentry: Dict[str, Any], tentry: Dict[str, Any],
                        native_los: Dict[str, Any]) -> str:
    """Name the terrain that plausibly blocks a LOS VASL reported blocked.

    Traced on the board grid from the firer's to the target's absolute
    height (hex ground elevation + VASL's level-in-hex). Same board only;
    empty string when nothing can be said.
    """
    from app.services import board_terrain  # local: avoids an import cycle
    fbase, flabel = _split_hex_key(fkey)
    tbase, tlabel = _split_hex_key(tkey)
    if fbase != tbase:
        return ""
    fh = ((fentry.get("terrain") or {}).get("elevation") or 0) + \
        int(native_los.get("source_level") or 0)
    th = ((tentry.get("terrain") or {}).get("elevation") or 0) + \
        int(native_los.get("target_level") or 0)
    try:
        trace = board_terrain.los_obstacle_trace(
            fbase, flabel, tlabel, fh, th, _board_transforms(state, fbase))
    except Exception as e:  # never let the trace break the error message
        logging.warning("los_obstacle_trace failed: %s", e)
        return ""
    if trace is None:
        return ""
    blockers = [c for c in trace if c["blocks"]]
    head = (f" Firer {fkey} is at level {fh}, target {tkey} at level {th} "
            "(hex elevation + level in hex).")
    if not blockers:
        return head + (
            " No terrain on the board's terrain grid reaches the LOS line "
            "between them, so VASL is likely blocking on counters (wrecks, "
            "SMOKE, blazes, rubble) or an overlay — check the map.")
    parts = []
    for c in blockers:
        what = c["terrain"]
        if what == "Orchard":
            what = ("in-season Orchard (a one-level obstacle to LOS to/from "
                    "a higher elevation)")
        parts.append(
            f"{tbase}-{c['hex']} {what} on level-{c['ground']} ground, "
            f"obstacle top level {c['top']:g} vs the LOS line at level "
            f"{c['los_height']:g} there ({c['rule']})")
    return head + (" Intervening obstacle(s) at or above the LOS line, "
                   "traced on the board's terrain grid: "
                   + "; ".join(parts) + ".")


_HEX_NAME = re.compile(r"([A-Z]{1,2}\d{1,2})$")


def _bare(name) -> str:
    """VASL hex name ('S3', '41S3', '41-S3') -> bare label 'S3'."""
    m = _HEX_NAME.search(str(name or "").strip().upper())
    return m.group(1) if m else str(name or "").strip().upper()


def _hexside_counters(facts: Dict[str, Any], warnings: List[str]):
    """(wall-like hexside counters, ["hex: name" not modelled]) from facts.

    Extension >= 0.6 sends every non-unit counter per hex ("hexes"), which
    counter_rules classifies; 0.5.x sent only a pre-digested "roadblocks".
    """
    from app.asl import counter_rules  # local: keeps the import graph flat
    hexes = [h for h in facts.get("hexes") or [] if isinstance(h, dict)]
    if not hexes:
        return [dict(rb, label="Roadblock", tem=2, rule="B29.2-.3")
                for rb in facts.get("roadblocks") or []
                if isinstance(rb, dict) and rb.get("toward")], []
    base_of = {_bare(h.get("hex")): h.get("base") for h in hexes}
    walls, unmodeled = [], []
    for hx in hexes:
        for c in hx.get("counters") or []:
            if not isinstance(c, dict):
                continue
            kind, entry = counter_rules.classify(c.get("name"))
            if kind == "hexside_wall":
                toward, how = counter_rules.hexside_from_counter(c, hx, entry)
                if not toward:
                    warnings.append(
                        f"{entry['label']} in {_bare(hx.get('hex'))}: can't tell "
                        f"which hexside it obstructs ({how}) — check the map.")
                    continue
                walls.append(dict(
                    hex=hx.get("hex"), toward=toward, method=how,
                    label=entry["label"], tem=entry["tem"], rule=entry["rule"],
                    hex_base=hx.get("base"), toward_base=base_of.get(_bare(toward))))
            elif kind == "unmodeled" and hx.get("role") in ("path", "target"):
                unmodeled.append(f"{_bare(hx.get('hex'))}: {c.get('name')}")
    return walls, unmodeled


def _apply_vasl_facts(facts: Dict[str, Any], base: str, flabel: str,
                      tlabel: str, tentry: Dict[str, Any], hindrance: int,
                      hexside_tem: int, hexside_tem_label: Optional[str],
                      assumptions: List[str], warnings: List[str]):
    """Let VASL's live map model (with overlays and SSR changes) decide the
    target hexside, roadblocks and hindrance sources.

    Returns (hexside_tem, hexside_tem_label, hindrance_label). Raises
    ValueError when a roadblock blocks a same-level LOS — VASL's LOS engine
    ignores roadblocks, so its "clear" can't be taken at face value there.
    """
    path = [p for p in facts.get("path") or [] if isinstance(p, dict)]
    seq = [flabel] + [_bare(p.get("hex")) for p in path] + [tlabel]
    fh, th = facts.get("source_height"), facts.get("target_height")

    # Hexside the LOS enters the target through (B9.3), straight from VASL.
    entry = facts.get("target_entry") or {}
    neighbor = _bare(entry.get("neighbor")) if entry.get("neighbor") else None
    entry_terr = (entry.get("terrain") or "").strip()
    if neighbor:
        if entry_terr in _HEXSIDE_TEM:
            tem, rule = _HEXSIDE_TEM[entry_terr]
            hexside_tem, hexside_tem_label = tem, (
                f"{entry_terr} hexside TEM +{tem} ({rule}: VASL's map shows "
                f"the LOS entering {tlabel} across its {neighbor}|{tlabel} "
                f"{entry_terr.lower()} hexside)")
        elif hexside_tem and entry_terr not in _HEXSIDE_TEM:
            # the board-file trace thought so, VASL's map (overlays/SSR
            # applied) says that hexside has no wall/hedge now
            assumptions.append(
                f"VASL's map shows no wall/hedge on the {neighbor}|{tlabel} "
                f"hexside the LOS enters through (map now: "
                f"{entry_terr or 'none'}); hexside TEM not applied.")
            hexside_tem, hexside_tem_label = 0, None

    # Wall-like hexside counters VASL's LOS ignores (roadblocks, B29), and
    # every counter the rules table doesn't know (counter_rules.py).
    walls, unmodeled = _hexside_counters(facts, warnings)
    for rb in walls:
        a, b = _bare(rb.get("hex")), _bare(rb.get("toward"))
        name, tem, rule = rb["label"], rb["tem"], rb["rule"]
        side = f"{a}|{b}"
        how = ("" if rb.get("method") == "position" else
               " (hexside read from the counter's arrow)")
        ha, hb = rb.get("hex_base"), rb.get("toward_base")
        if ha is not None and hb is not None and ha != hb:
            assumptions.append(
                f"The {name.lower()} hexside {side} joins hexes of different "
                f"base levels ({a} level {ha}, {b} level {hb}): it is at the "
                "higher level and treated like a Hillside Wall (B29.4, B9.6).")
        if {a, b} == {tlabel, neighbor}:
            if tem >= hexside_tem:
                hexside_tem, hexside_tem_label = tem, (
                    f"{name} TEM +{tem} ({rule}: treated as a stone wall across "
                    f"{side}, the hexside the LOS enters {tlabel} through)")
            assumptions.append(f"{name} on {side}{how}.")
            continue
        crossed = any({seq[i], seq[i + 1]} == {a, b} for i in range(len(seq) - 1))
        if not crossed:
            assumptions.append(f"{name} on {side} is not crossed by this "
                               f"LOS{how}.")
            continue
        if flabel in (a, b):
            assumptions.append(
                f"LOS leaves {flabel} across its own {name.lower()} hexside "
                f"{side}: no effect (B9.2: a hexside of the viewing hex){how}.")
            continue
        if fh is not None and th is not None and fh == th:
            raise ValueError(
                f"LOS is blocked by the {name.lower()} on {side}: it is a "
                f"Half-Level Obstacle to same-level LOS ({rule}, B9.2), and "
                f"firer and target are both at level {fh}. VASL's own LOS "
                f"check ignores {name.lower()}s, so it reported this LOS "
                f"clear{how}.")
        assumptions.append(
            f"LOS crosses the {name.lower()} on {side}; firer (level {fh}) and "
            f"target (level {th}) are at different levels, so its same-level "
            f"Half-Level block ({rule}) does not apply{how}.")
    if unmodeled:
        warnings.append(
            "Counters on the LOS / in the target hex that this calculator does "
            "not model — check whether they change LOS or TEM: "
            + "; ".join(unmodeled) + ".")

    # Where VASL's hindrance comes from (A6.7): counters first, then terrain.
    label = None
    if hindrance:
        from app.services.board_terrain import LOS_HINDRANCES
        sources = [f"{_bare(p.get('hex'))} ({', '.join(p['counters'])})"
                   for p in path if p.get("counters")]
        sources += [f"{_bare(p.get('hex'))} ({p.get('terrain')})" for p in path
                    if not p.get("counters") and p.get("terrain") in LOS_HINDRANCES]
        if sources:
            label = (f"LOS Hindrance +{hindrance} (A6.7, VASL LOS check): "
                     + "; ".join(sources))
        else:
            first = facts.get("first_hindrance_hex")
            label = (f"LOS Hindrance +{hindrance} (A6.7, VASL LOS check)"
                     + (f": begins in {_bare(first)}" if first else "")
                     + " — source not identified on the map")
            warnings.append(
                f"VASL counted a +{hindrance} hindrance"
                + (f" beginning in {_bare(first)}" if first else "")
                + " but no SMOKE, vehicle, wreck or hindrance terrain was found "
                "there; check that hex on the map.")

    # VASL's terrain at the target Location (overlays/SSR applied) vs ours.
    vt = (facts.get("target_terrain") or "").strip()
    ours = (tentry.get("terrain") or {}).get("parts") or []
    if vt and ours and vt not in ours and vt != "Open Ground":
        warnings.append(
            f"VASL's map shows {tlabel}'s Location terrain as {vt}, but the "
            f"board file reads {' + '.join(ours)} (an overlay or SSR may have "
            "changed it): the target TEM below uses the board-file terrain.")
    return hexside_tem, hexside_tem_label, label


def _grid_los(state: Dict[str, Any], base: str, flabel: str, tlabel: str,
              fentry: Dict[str, Any], tentry: Dict[str, Any],
              assumptions, warnings: List[str],
              out: Dict[str, Any]) -> None:
    """LOS obstacles + Hindrances traced on the board grid, for questions
    VASL's own LOS check didn't cover. Sets the "los" / "hindrance" caveats, adds a
    BLOCKED warning, and puts any hindrance DRM in out["hindrance"/"label"].
    """
    from app.services import board_terrain  # local: avoids an import cycle
    fh = (fentry.get("terrain") or {}).get("elevation") or 0
    th = (tentry.get("terrain") or {}).get("elevation") or 0
    transforms = _board_transforms(state, base)
    try:
        obstacles = board_terrain.los_obstacle_trace(
            base, flabel, tlabel, fh, th, transforms)
        hindrances = board_terrain.los_hindrance_trace(
            base, flabel, tlabel, fh, th, transforms)
    except Exception as e:  # never let the trace break the resolution
        logging.warning("grid LOS trace failed: %s", e)
        return
    if obstacles is None or hindrances is None:
        return  # no board data: keep the generic assumptions
    blockers = [c for c in obstacles if c["blocks"]]
    if blockers:
        warnings.append(
            "LOS traced on the board's terrain grid looks BLOCKED by: "
            + "; ".join(f"{base}-{c['hex']} {c['terrain']} (obstacle top level "
                        f"{c['top']:g} vs the LOS line at {c['los_height']:g}, "
                        f"{c['rule']})" for c in blockers)
            + ". VASL's own LOS check was not sent with this question — name "
            "both hexes in the question so VASL can confirm.")
    assumptions.set("los",
        f"LOS traced on the board's terrain grid from level {fh} to level {th} "
        "(VASL's LOS check not available for this pair): "
        + ("see the BLOCKED warning" if blockers
           else "no terrain obstacle reaches the LOS line")
        + "; wall/hedge hexsides traced separately (B9). Counters on the map "
        "(SMOKE, wrecks, blazes) are not seen.")
    if hindrances:
        names = ", ".join(f"{base}-{h['hex']} {h['terrain']}" for h in hindrances)
        out["hindrance"] = len(hindrances)
        out["label"] = f"LOS Hindrance +{len(hindrances)} (A6.7): {names}"
        assumptions.set("hindrance", f"Intervening LOS Hindrances traced on "
                        f"the board's terrain grid: {names}.")
    else:
        assumptions.set("hindrance", "No intervening terrain LOS Hindrances "
                        "on the board's terrain grid.")


def _los_hexsides(state: Dict[str, Any], base: str, flabel: str,
                  tlabel: str):
    """Wall/hedge hexsides crossed by the LOS (same board only), or None."""
    from app.services import board_terrain  # local: avoids an import cycle
    transforms = _board_transforms(state, base)
    try:
        return board_terrain.los_hexside_crossings(
            str(base), flabel, tlabel, transforms)
    except Exception as e:  # never let the trace break the resolution
        logging.warning("los_hexside_crossings failed: %s", e)
        return None

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


def _native_los_matches_attack(native_los: Dict[str, Any], fkey: str, tkey: str) -> bool:
    """Return True only when a VASL LOS payload belongs to this exact attack.

    Bare VASL hex labels are ambiguous on multi-board layouts. Treat them as
    usable only for same-board attacks; cross-board attacks need board-qualified
    endpoint ids from the client or the LOS could come from the wrong physical
    pair of hexes.
    """
    source = str(native_los.get("source", "")).strip().upper()
    target = str(native_los.get("target", "")).strip().upper()
    if not source or not target:
        return False
    fk = fkey.upper()
    tk = tkey.upper()
    if source == fk and target == tk:
        return True
    fbase, flabel = _split_hex_key(fkey)
    tbase, tlabel = _split_hex_key(tkey)
    if source == flabel and target == tlabel and fbase == tbase:
        return True
    return False


def _unmodeled_board_ssrs_for_attack(state: Dict[str, Any], fkey: str, tkey: str) -> List[str]:
    bases = {_split_hex_key(fkey)[0], _split_hex_key(tkey)[0]}
    out: List[str] = []
    for b in state.get("boards", []):
        base = str(b.get("base") or b.get("name", "")).lstrip("r").upper()
        if base not in bases:
            continue
        for transform in b.get("ssr_transforms") or []:
            try:
                from app.services import board_terrain
                modeled = transform in board_terrain.SSR_NAME_TRANSFORMS
            except Exception:
                modeled = False
            if not modeled and transform not in out:
                out.append(transform)
    return out


def _empty_hex_entry(state: Dict[str, Any], hex_id: str) -> Tuple[str, Dict[str, Any]]:
    """Key and a synthetic (unit-less) entry for a real board hex with no units.

    Lets the resolver price a hypothetical target ("what if A3 fires at A6")
    from the hex's terrain. Never mutates `state`.
    """
    hid = hex_id.strip().upper()
    base, sep, label = hid.partition("-")
    if not sep:
        base, label = "", hid
    if not re.fullmatch(r"[A-Z]{1,3}\d{1,2}", label):
        raise ValueError(f"Hex {hex_id!r} is not a valid hex id (e.g. '57-H9').")
    boards = {str(b.get("base") or b.get("name", "")).lstrip("r").upper(): b
              for b in state.get("boards", [])}
    if base:
        if base not in boards:
            raise ValueError(f"Board {base!r} is not in the save (boards: {sorted(boards)}).")
    elif len(boards) == 1:
        base = next(iter(boards))
    else:
        raise ValueError(
            f"Hex {hex_id!r} has no units and the save has several boards "
            f"{sorted(boards)}. Use the full '<board>-<hex>' id."
        )
    key = f"{base}-{label}"
    entry: Dict[str, Any] = {"units": [], "markers": [], "empty": True}
    try:
        from app.services import board_terrain  # local: avoids an import cycle
        terrain = board_terrain.get_hex_terrain(
            base, label, boards[base].get("ssr_transforms", []))
    except Exception as e:
        logging.warning("resolve_attack: terrain lookup for empty %s failed: %s", key, e)
        terrain = None
    if terrain:
        entry["terrain"] = terrain
    return key, entry


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


def _is_squad(cls: Dict[str, Any]) -> bool:
    return cls.get("kind") == "personnel" and "sq" in (cls.get("detail") or "").lower()


def _is_hs_or_crew(cls: Dict[str, Any]) -> bool:
    detail = (cls.get("detail") or "").lower()
    return cls.get("kind") == "personnel" and ("hs" in detail or "cr" in detail or "crew" in detail)


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
                 assumptions: List[str],
                 hexside_checked: bool = False) -> Tuple[int, str]:
    """(TEM, label) for the target hex's terrain (no entrenchment).

    hexside_checked: the LOS was traced for wall/hedge hexsides, so a
    hexside terrain showing up in the hex sample needs no generic warning.
    """
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
            if not hexside_checked:
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
    native_los: Optional[Dict[str, Any]] = None,
    selected_firers: Optional[List[str]] = None,
    selected_targets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolve a fire attack between two hexes of a parsed .vsav state.

    Returns an itemized, auditable derivation (firers, fp/drm breakdowns,
    assumptions, warnings) with the full `ift.compute_attack` output embedded
    under "ift". Raises ValueError for unusable inputs (bad hex ids, no
    eligible firers, ...). Never mutates `state`.

    The rules run as an ordered list of steps: app/asl/attack/pipeline.py.
    """
    from app.asl.attack.pipeline import resolve  # local: the steps import this module
    return resolve(state, firing_hex, target_hex, phase, firing_unit_filter,
                   native_los, selected_firers, selected_targets)
