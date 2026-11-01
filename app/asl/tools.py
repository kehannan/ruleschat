"""
ASL Agentic Tools

Thin wrappers exposing the deterministic UI calculator — the Infantry Fire
Table odds engine (`app.asl.ift`) — as functions the LLM can call via OpenAI's
function calling.

The same engine powers the `/ift` page, so an agentic answer and the
standalone tool always agree. Tool schemas pull their enums live from the
engine, so they can never drift from the underlying tables.
"""
import logging
from typing import Dict, Any, List, Optional

from app.asl import ift
from app.asl import attack_resolver
from app.asl import cc_resolver
from app.asl import blind_hexes as blind_hexes_engine
from app.asl import rules_lookup
from app.asl import thtk


# =============================================================================
# Tool Functions
# =============================================================================


def ift_odds(
    column: int,
    drm: int = 0,
    cowering: str = "none",
    san: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Infantry Fire Table outcome probabilities for one attack.

    Args:
        column: Final FP column — one of `ift.valid_columns()`.
        drm: Total DR modifier (negative favors the firer).
        cowering: "none", "regular" (doubles shift 1 column left), or
                  "double" (Conscripts etc., 2 columns left).
        san: Enemy Sniper Activation Number (2–12), or None to skip.

    Returns the result distribution. The heavy per-cell heatmap grid (used only
    by the UI) is stripped to keep the tool output compact for the model.
    """
    result = ift.compute_distribution(column=column, drm=drm, cowering=cowering, san=san)
    result.pop("cells", None)  # UI-only heatmap data; not useful to the model
    logging.info(
        "🎲 ift_odds(col=%s, drm=%s, cowering=%s, san=%s) -> %d outcomes",
        column, drm, cowering, san, len(result.get("distribution", [])),
    )
    return result


def ift_attack(
    units: List[Dict[str, Any]],
    afph: bool = False,
    opportunity_fire: bool = False,
    area_fire_halvings: int = 0,
    tem: int = 0,
    hindrance: int = 0,
    ffnam: bool = False,
    ffmo: bool = False,
    leadership: int = 0,
    encircled_firer: bool = False,
    other_drm: Optional[List[Dict[str, Any]]] = None,
    inexperienced: bool = False,
    firer_cowering_exempt: bool = False,
    san: Optional[int] = None,
    target: Optional[Dict[str, Any]] = None,
    range_to_target: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build and resolve a full IFT attack from the situation (A7.2–.36).

    Resolves per-unit firepower modification, the FP column, an itemized DRM
    ledger (with FFMO validation per A4.6), auto-derived cowering, the result
    distribution, and optional break/pin/casualty odds vs a target. See
    `ift.compute_attack` for the full contract.

    Returns the computation chain (fp_breakdown / drm_breakdown / warnings)
    plus the distribution, so the model can cite the math verbatim. The
    UI-only heatmap grid is stripped to keep the tool output compact.
    """
    result = ift.compute_attack(
        units=units,
        afph=afph,
        opportunity_fire=opportunity_fire,
        area_fire_halvings=area_fire_halvings,
        tem=tem,
        hindrance=hindrance,
        ffnam=ffnam,
        ffmo=ffmo,
        leadership=leadership,
        encircled_firer=encircled_firer,
        other_drm=other_drm,
        inexperienced=inexperienced,
        firer_cowering_exempt=firer_cowering_exempt,
        san=san,
        target=target,
        range_to_target=range_to_target,
    )
    result.pop("cells", None)  # UI-only heatmap data; not useful to the model
    logging.info(
        "🧮 ift_attack(%d unit(s), afph=%s, area=%s) -> %s FP, col %s, drm %s, cowering %s",
        len(units), afph, area_fire_halvings,
        result.get("total_fp"), result.get("column"), result.get("drm"),
        result.get("cowering"),
    )
    return result


def resolve_attack(
    firing_hex: str,
    target_hex: str,
    phase: Optional[str] = None,
    firing_unit_filter: Optional[str] = None,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a fire attack directly from the attached .vsav's parsed state.

    Unlike ift_attack, this takes HEX IDs — not FP/TEM/DRM numbers — and
    derives every input deterministically from the save (see
    app.asl.attack_resolver). Requires the parsed vsav state, which is
    threaded in server-side via `_context` (never exposed to the model);
    without an attached save it returns a clear error directing the model
    to ift_attack instead.
    """
    state = (_context or {}).get("vsav_state")
    if not state:
        return {
            "error": (
                "No VASL .vsav save is attached to this message, so there is "
                "no parsed board state to resolve against. Use the ift_attack "
                "tool instead, supplying the firing units' FP and the "
                "TEM/DRM explicitly (cite the rules for each value)."
            )
        }
    try:
        result = attack_resolver.resolve_attack(
            state,
            firing_hex=firing_hex,
            target_hex=target_hex,
            phase=phase or _phase_from_vasl((_context or {}).get("game_phase")),
            firing_unit_filter=firing_unit_filter,
            selected_firers=(_context or {}).get("selected_firers"),
            selected_targets=(_context or {}).get("selected_targets"),
            native_los=(_context or {}).get("native_los"),
        )
    except ValueError as e:
        return {"error": str(e)}
    logging.info(
        "🎯 resolve_attack(%s -> %s, phase=%s) -> %s FP, col %s, drm %s",
        firing_hex, target_hex, phase,
        result.get("total_fp"), result.get("column"), result.get("drm"),
    )
    return result


def _phase_from_vasl(phase: Optional[str]) -> str:
    """Map the VASL phase-wheel label to the resolver's fire-phase names."""
    # Defensive First Fire happens during the enemy MPh; the DFPh is Final Fire.
    labels = {
        "PFPh": "prep",
        "MPh": "defensive_first",
        "DFPh": "defensive_final",
        "AFPh": "advancing",
    }
    return labels.get((phase or "").strip(), "prep")


def resolve_cc(
    hex_id: str,
    attacker_side: Optional[str] = None,
    attacker_filter: Optional[str] = None,
    defender_filter: Optional[str] = None,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve the Close Combat in one hex directly from the attached .vsav's
    parsed state.

    Takes a HEX ID — not FP/odds/DRM numbers — and derives the full A11
    derivation deterministically (see app.asl.cc_resolver), including BOTH
    directions since CC is simultaneous. Requires the parsed vsav state,
    threaded in server-side via `_context` (never exposed to the model);
    without an attached save it returns a clear error: there is no
    no-save CC tool, so the model must derive carefully with citations.
    """
    state = (_context or {}).get("vsav_state")
    if not state:
        return {
            "error": (
                "No VASL .vsav save is attached to this message, so there is "
                "no parsed board state to resolve against. There is no "
                "non-save Close Combat tool: derive the CC carefully by "
                "hand, citing every value via file_search (A11.11 CCT odds "
                "rounded DOWN + Kill Numbers; SMC CC FP = 1, A11.14; "
                "SW/ordnance NA in CC, A11.13; broken units never attack, "
                "A11.16), and tell the user that attaching the .vsav save "
                "would give exact, deterministic numbers."
            )
        }
    try:
        result = cc_resolver.resolve_cc(
            state,
            hex_id=hex_id,
            attacker_side=attacker_side,
            attacker_filter=attacker_filter,
            defender_filter=defender_filter,
        )
    except ValueError as e:
        return {"error": str(e)}
    fwd = result["attacks"][0]
    logging.info(
        "⚔️ resolve_cc(%s, attacker=%s) -> %s (KN %s, drm %s)",
        hex_id, attacker_side, fwd.get("odds"), fwd.get("kill_number"),
        fwd.get("drm"),
    )
    return result


def cc_attack(
    attack_fp: float,
    defense_fp: float,
    drm: int = 0,
    hth: bool = False,
    defender_types: Optional[List[str]] = None,
    other_drm: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    No-save Close Combat calculator (A11.11) — from already-summed CC
    firepowers, with no .vsav.

    Returns the CCT odds column, Kill Number, an itemized DRM ledger, the
    Original DR needed to eliminate / cause Casualty Reduction, and 2d6
    probabilities. Shares the engine with `resolve_cc`, so both always agree.
    """
    result = cc_resolver.compute_cc(
        attack_fp=attack_fp,
        defense_fp=defense_fp,
        drm=drm,
        hth=hth,
        defender_types=defender_types,
        drm_breakdown=other_drm,
    )
    logging.info(
        "⚔️ cc_attack(%s vs %s, drm=%s, hth=%s) -> %s, KN %s, p_elim %s",
        attack_fp, defense_fp, result.get("drm"), hth,
        result.get("odds"), result.get("kill_number"), result.get("p_eliminate"),
    )
    return result


def blind_hexes(
    firer_level: int,
    obstacle_hex_level: int,
    range_to_obstacle: int,
    hexes_behind: List[int],
    obstacle_height: int = 0,
    cliff: bool = False,
) -> Dict[str, Any]:
    """
    Blind Hex calculator (A6.4-.43, B10.23, B11.21): which hexes directly
    behind one obstacle are blind to a higher-elevation firer.

    Returns per-hex verdicts plus a rule-cited, plain-English `steps` ledger
    so the model can present the derivation verbatim. See
    `app.asl.blind_hexes.compute_blind_hexes` for the full contract.
    """
    result = blind_hexes_engine.compute_blind_hexes(
        firer_level=firer_level,
        obstacle_hex_level=obstacle_hex_level,
        range_to_obstacle=range_to_obstacle,
        hexes_behind=hexes_behind,
        obstacle_height=obstacle_height,
        cliff=cliff,
    )
    logging.info(
        "🏔️ blind_hexes(firer=%s, obstacle=%s+%s, range=%s, cliff=%s, behind=%s) -> blind %s",
        firer_level, obstacle_hex_level, obstacle_height, range_to_obstacle, cliff,
        hexes_behind, result.get("blind_hexes"),
    )
    return result


def thtk_odds(
    modified_th: int,
    drm: int = 0,
    target_type: str = "vehicle",
    final_tk: Optional[int] = None,
    turret_final_tk: Optional[int] = None,
    ch_final_tk: Optional[int] = None,
    he: bool = False,
    unarmored: bool = False,
    hull_down: bool = False,
    multiple_hits: bool = False,
    ift_fp: Optional[float] = None,
    ch_ift_fp: Optional[float] = None,
    effects_drm: int = 0,
) -> Dict[str, Any]:
    """
    Ordnance To Hit / To Kill odds when the Modified TH#, total TH DRM and the
    Final TK# (or IFT FP vs personnel) are already known. See
    `thtk.quick_odds`.
    """
    try:
        result = thtk.quick_odds(
            modified_th=modified_th, drm=drm, target_type=target_type,
            final_tk=final_tk, turret_final_tk=turret_final_tk, ch_final_tk=ch_final_tk,
            he=he, unarmored=unarmored, hull_down=hull_down, multiple_hits=multiple_hits,
            ift_fp=ift_fp, ch_ift_fp=ch_ift_fp, effects_drm=effects_drm,
        )
    except ValueError as e:
        return {"error": str(e)}
    logging.info("🎯 thtk_odds(TH#%s, drm %s, TK %s) -> hit %s",
                 modified_th, drm, final_tk, result.get("p_hit"))
    return result


def ordnance_attack(**kwargs) -> Dict[str, Any]:
    """
    Build and resolve one ordnance shot from the SITUATION (ASL Chapter C):
    gun + ammo + range + target -> Modified TH# (C3/C4), an itemized TH DRM
    ledger from the C5/C6 cases, the 36-roll hit tree (CH, Multiple/Improbable
    Hits, hull/turret) and the To Kill / IFT outcome distribution. See
    `thtk.compute_attack`.
    """
    try:
        result = thtk.compute_attack(**kwargs)
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
    logging.info(
        "💥 ordnance_attack(%s %s, %s target @%s) -> TH# %s drm %s, hit %s",
        result.get("gun"), result.get("ammo"), result.get("target_type"),
        result.get("range"), result["to_hit"].get("modified_th"),
        result["to_hit"].get("total_drm"), result["to_hit"].get("p_hit"),
    )
    return result


def get_section(
    section: str,
    include_subsections: bool = False,
) -> Dict[str, Any]:
    """
    Deterministic rulebook lookup: exact text of one rule section (plus any
    official Q&A / errata entries for it). Backed by the extracted stores in
    data/rulebook/ (see app.asl.rules_lookup).
    """
    result = rules_lookup.get_section(
        section, include_subsections=include_subsections, include_qa=True
    )
    logging.info(
        "📖 get_section(%s%s) -> %s",
        section, ", +subs" if include_subsections else "",
        result.get("section") or result.get("error", "?"),
    )
    return result


_SEARCH_RULES_MAX = 12
_SEARCH_CHUNK_CHAR_CAP = 1500


def search_rules(
    query: str,
    max_results: int = 8,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Follow-up semantic search over the rulebook + Q&A vector stores.

    Exposed only on the OpenRouter agentic path (the OpenAI path has the
    hosted file_search tool mid-loop). Requires the service to thread
    `retrieval_client` and `vector_store_ids` into the tool context.
    """
    if not query or not query.strip():
        return {"error": "empty query"}
    ctx = _context or {}
    client = ctx.get("retrieval_client")
    store_ids = ctx.get("vector_store_ids")
    if client is None or not store_ids:
        return {"error": "search_rules unavailable: no retrieval client in tool context"}
    from app.asl.retrieval import retrieve_chunks  # local import: avoids cycle at module load
    n = max(1, min(int(max_results), _SEARCH_RULES_MAX))
    chunks = retrieve_chunks(client, store_ids, query=query, max_results_per_store=n)
    chunks.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    out = [
        {
            "text": c["text"][:_SEARCH_CHUNK_CHAR_CAP],
            "filename": c.get("filename", ""),
            "score": round(c.get("score", 0.0), 4),
        }
        for c in chunks[:n]
    ]
    logging.info("🔎 search_rules(%r, n=%d) -> %d chunks", query[:60], n, len(out))
    return {"chunks": out}


# =============================================================================
# Tool Schemas (OpenAI Responses API function calling) — enums pulled live
# =============================================================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "ift_odds",
        "description": (
            "Compute Infantry Fire Table (IFT) outcome probabilities for a small-arms / "
            "MG / HE attack. Use whenever a question asks the chance of a given result "
            "(K, KIA, MC, NMC, PTC, etc.) for an attack at a known FP column and DRM. "
            "Returns the probability of each result over all 36 dice combinations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "column": {
                    "type": "integer",
                    "enum": ift.valid_columns(),
                    "description": "Final Firepower column on the IFT.",
                },
                "drm": {
                    "type": "integer",
                    "description": "Total Die Roll Modifier (negative favors the firer).",
                },
                "cowering": {
                    "type": "string",
                    "enum": list(ift.COWERING_SHIFT.keys()),
                    "description": (
                        "Cowering mode: 'none', 'regular' (doubles shift 1 column left), "
                        "or 'double' (Conscripts etc., 2 columns left)."
                    ),
                },
                "san": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 12,
                    "description": "Enemy Sniper Activation Number (2–12). Omit to skip the sniper calc.",
                },
            },
            "required": ["column"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ift_attack",
        "description": (
            "Build a full Infantry Fire Table attack from the SITUATION: computes each "
            "firing unit's adjusted FP per A7.2-.36 (PBF/TPBF, long range, AFPh, area "
            "fire, pinned, assault fire), picks the FP column, assembles an itemized DRM "
            "(TEM, hindrance, FFNAM/FFMO with A4.6 validation, leadership, encirclement), "
            "derives cowering, and returns the result distribution — plus break/pin/"
            "casualty odds vs a target morale, or kill odds vs an unarmored vehicle. Use "
            "whenever a question DESCRIBES the situation (units, range, terrain, "
            "movement) rather than an already-known FP column; if the final column and "
            "total DRM are already given, use ift_odds instead. Standard IFT only (no "
            "IIFT); no LOS/terrain modeling — supply TEM/hindrance values yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "units": {
                    "type": "array",
                    "minItems": 1,
                    "description": (
                        "Firing units. A squad firing a SW it mans is two entries "
                        "(squad inherent FP + the SW's FP)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "fp": {
                                "type": "number",
                                "description": "Printed FP of the unit or weapon.",
                            },
                            "pbf": {
                                "type": "string",
                                "enum": list(ift.PBF_MULTIPLIER.keys()),
                                "description": (
                                    "Point Blank Fire: 'pbf' = adjacent (x2), 'tpbf' = "
                                    "same Location (x3). Small arms/MG/ATR/IFE only (A7.21)."
                                ),
                            },
                            "long_range": {
                                "type": "boolean",
                                "description": (
                                    "Firing at long range — FP halved (A7.22). PREFER supplying "
                                    "normal_range + the top-level range_to_target instead and let "
                                    "the tool derive this; a derived value overrides this flag."
                                ),
                            },
                            "normal_range": {
                                "type": "integer",
                                "description": (
                                    "The firer's Normal Range — the MIDDLE factor of an MMC "
                                    "(a 5-4-8 is 4; a 6-6-6 is 6; a 4-6-7 is 6), or a SW's range "
                                    "factor. With the top-level range_to_target, the tool derives "
                                    "Long Range Fire itself (range_to_target > normal_range)."
                                ),
                            },
                            "pinned": {
                                "type": "boolean",
                                "description": "Firer is pinned — FP halved (A7.8).",
                            },
                            "assault_fire": {
                                "type": "boolean",
                                "description": (
                                    "Underscored-FP unit using Assault Fire in AFPh: +1 FP "
                                    "after all other modification, then round up (A7.36). "
                                    "NA at long range or with opportunity fire."
                                ),
                            },
                        },
                        "required": ["fp"],
                        "additionalProperties": False,
                    },
                },
                "range_to_target": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Range in hexes from firer to target. Supply this together with each "
                        "unit's normal_range and the tool derives Long Range Fire per A7.22 "
                        "(range_to_target > normal_range → FP halved) — do NOT hand-judge "
                        "long_range yourself, that is exactly where derivation goes wrong."
                    ),
                },
                "afph": {
                    "type": "boolean",
                    "description": "Advancing Fire Phase — all FP halved unless opportunity fire (A7.24).",
                },
                "opportunity_fire": {
                    "type": "boolean",
                    "description": "Opportunity Fire — negates the AFPh halving; assault fire NA (A7.25).",
                },
                "area_fire_halvings": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Number of attack-wide area-fire halvings (concealed target, "
                        "spraying fire, ...). Each halves every unit again (A7.23, A9.5)."
                    ),
                },
                "tem": {
                    "type": "integer",
                    "description": "Target Location TEM (e.g. +2 stone building, -1 open-ground FFMO handled separately).",
                },
                "hindrance": {
                    "type": "integer",
                    "description": "Total LOS hindrance DRM (smoke, grain, intervening hexes...).",
                },
                "ffnam": {
                    "type": "boolean",
                    "description": "First Fire vs Non-Assault Movement: -1 (Defensive First Fire only, A4.6).",
                },
                "ffmo": {
                    "type": "boolean",
                    "description": (
                        "First Fire vs Moving in Open ground: -1. Automatically dropped "
                        "with a warning if any hindrance or positive TEM applies (A4.6)."
                    ),
                },
                "leadership": {
                    "type": "integer",
                    "description": "Directing leader's DRM, e.g. -2 (A7.531). Any non-zero value also prevents cowering.",
                },
                "encircled_firer": {
                    "type": "boolean",
                    "description": "Firer is encircled: +1 to its attacks (A7.7).",
                },
                "other_drm": {
                    "type": "array",
                    "description": "Any other DRM as labeled line items (air bursts, CX, ...).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "drm": {"type": "integer"},
                        },
                        "required": ["label", "drm"],
                        "additionalProperties": False,
                    },
                },
                "inexperienced": {
                    "type": "boolean",
                    "description": "Inexperienced/conscript firer — cowers two columns (A7.9, A19.33).",
                },
                "firer_cowering_exempt": {
                    "type": "boolean",
                    "description": (
                        "Firer never cowers: SMC, berserk/fanatic, British Elite/1st-line, "
                        "Finn, vehicular/IFE fire, fire lane... (A7.9)."
                    ),
                },
                "san": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 12,
                    "description": "Enemy Sniper Activation Number (2-12). Omit to skip the sniper calc.",
                },
                "target": {
                    "type": "object",
                    "description": (
                        "Optional target for outcome odds. kind 'personnel' needs morale; "
                        "kind 'vehicle' (unarmored, A7.308) uses the IFT vehicle-line kill numbers."
                    ),
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["personnel", "vehicle"],
                        },
                        "morale": {
                            "type": "integer",
                            "description": "Target's current morale level (personnel only).",
                        },
                        "mc_drm": {
                            "type": "integer",
                            "description": "DRM on the target's MC DR, e.g. -1 leader in its Location (personnel only).",
                        },
                        "encircled": {
                            "type": "boolean",
                            "description": "Target is encircled: morale lowered by 1 vs this attack (A7.7, personnel only).",
                        },
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            },
            "required": ["units"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "resolve_attack",
        "description": (
            "Resolve an Infantry Fire Table attack DIRECTLY from the attached VASL "
            ".vsav save's parsed board state. Takes hex IDs (use the '<board>-<hex>' "
            "IDs from the BOARD STATE block, e.g. '57-H9') — NOT firepower or DRM "
            "numbers — and deterministically derives the eligible firers, each unit's "
            "printed FP (counter values + a SW table), per-squad Assault Fire "
            "(A7.36, applied in the advancing phase from the counter's underscored "
            "FP) and Spraying Fire capability (A7.34, noted only), the hex range "
            "with PBF/TPBF doubling and long-range halving, an itemized rule-cited "
            "DRM ledger "
            "(entrenchment vs terrain TEM, leadership, CX), cowering, and the full "
            "IFT odds, plus explicit assumptions (LOS assumed clear EXCEPT wall/hedge "
            "hexsides, which are traced on the board's LOS grid and applied as TEM "
            "per B9.3 when the LOS enters the target hex across them; intervening "
            "hindrances not counted). ALWAYS prefer this over ift_attack when a .vsav "
            "is attached and the question asks to resolve fire between hexes. If no "
            "save is attached it returns an error — then fall back to ift_attack with "
            "explicit inputs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "firing_hex": {
                    "type": "string",
                    "description": "Hex the attack is fired FROM, e.g. '57-H9'.",
                },
                "target_hex": {
                    "type": "string",
                    "description": (
                        "Hex being attacked, e.g. '57-H8'. May be an EMPTY hex for "
                        "a what-if attack: FP column and DRM are then priced from "
                        "its terrain, with no break/pin odds."
                    ),
                },
                "phase": {
                    "type": "string",
                    "enum": list(attack_resolver.VALID_PHASES),
                    "description": (
                        "Fire phase: 'prep' (PFPh), 'advancing' (AFPh, FP halved; "
                        "Assault Fire +1 auto-applied for capable squads), "
                        "'defensive_first' (enemy MPh) or 'defensive_final' (DFPh)."
                    ),
                },
                "firing_unit_filter": {
                    "type": "string",
                    "description": (
                        "Optional substring to restrict which units fire (e.g. "
                        "'6-4-8' to fire only the squad, or 'LMG'). Leaders are "
                        "always considered for direction."
                    ),
                },
            },
            "required": ["firing_hex", "target_hex"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "cc_attack",
        "description": (
            "Compute a CLOSE COMBAT (A11.11) resolution WITHOUT a .vsav save: from "
            "the attackers' total CC firepower and the defenders' CC firepower, returns "
            "the odds ratio rounded DOWN to the CCT column, its Kill Number, an itemized "
            "DRM ledger, the Original DR needed to eliminate / cause Casualty Reduction, "
            "and 2d6 probabilities. Use for any CC odds or 'what must I roll to "
            "eliminate/capture' question when NO save is attached (with a .vsav, use "
            "resolve_cc instead). CC FP = sum of printed FP for each MMC, 1 per SMC "
            "(A11.14); broken units never attack (A11.16); SW/ordnance NA (A11.13); halve "
            "vs a concealed target (A11.19) — fold these into the FP you supply. Supply CC "
            "DRMs (leadership A11.141 = negative; broken defender -2 A11.16; CX +1 A4.51; "
            "Hero -1 A15.24; capture attempt +1 A20.21) via drm or other_drm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "attack_fp": {
                    "type": "number",
                    "description": "Total CC FP of the attacker(s), already summed (SMC = 1).",
                },
                "defense_fp": {
                    "type": "number",
                    "description": "Total CC FP of the defender(s).",
                },
                "drm": {
                    "type": "integer",
                    "description": (
                        "Net CC DR modifier (negative favors the attacker). Ignored "
                        "if other_drm is supplied."
                    ),
                },
                "other_drm": {
                    "type": "array",
                    "description": "Itemized CC DRMs; their drm values are summed.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "drm": {"type": "integer"},
                        },
                        "required": ["drm"],
                        "additionalProperties": False,
                    },
                },
                "hth": {
                    "type": "boolean",
                    "description": (
                        "Hand-to-Hand CC in effect — use the red Kill Number (J2.31). "
                        "Default uses the black KN."
                    ),
                },
                "defender_types": {
                    "type": "array",
                    "description": (
                        "Defender unit type(s) for A7.302 Casualty-Reduction-eliminates "
                        "semantics."
                    ),
                    "items": {
                        "type": "string",
                        "enum": ["squad", "hs", "crew", "smc"],
                    },
                },
            },
            "required": ["attack_fp", "defense_fp"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "resolve_cc",
        "description": (
            "Resolve the CLOSE COMBAT (A11) in one hex DIRECTLY from the attached "
            "VASL .vsav save's parsed board state. Takes the '<board>-<hex>' ID from "
            "the BOARD STATE block (e.g. '57-G9') — NOT firepower/odds/DRM numbers — "
            "and deterministically derives, for BOTH directions (CC is simultaneous, "
            "A11.1): each unit's CC FP with cites (printed FP for MMC; inherent FP 1 "
            "for SMC per A11.14; SW/ordnance never used, A11.13; broken attackers "
            "excluded, A11.16), the odds ratio rounded down to the CCT column with "
            "its black Kill Number (eliminate on Final DR < KN, Casualty Reduction "
            "on = KN — and CR ELIMINATES a HS/crew per A7.302), an itemized CC DRM "
            "ledger (leadership A11.141, CX A4.51, skiers E4.5, broken defenders "
            "A11.16), 2d6 probabilities, Melee-marker notes (Ambush NA in Melee), "
            "and explicit assumptions (no Ambush dr, no Hand-to-Hand, withdrawal not "
            "modeled). ALWAYS use this instead of hand-deriving CC math when a .vsav "
            "is attached and the question concerns CC/Melee in a hex. If no save is "
            "attached it returns an error; for CC questions without a save use the "
            "cc_attack tool instead (supply the CC firepowers and DRMs yourself)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hex_id": {
                    "type": "string",
                    "description": "The CC Location, e.g. '57-G9'.",
                },
                "attacker_side": {
                    "type": "string",
                    "description": (
                        "Side whose attack to list first (e.g. 'Russian'). "
                        "Optional: both directions are always resolved."
                    ),
                },
                "attacker_filter": {
                    "type": "string",
                    "description": (
                        "Optional comma-separated name substrings restricting which "
                        "attacker-side units take part (e.g. '2-3-7,COM'). Default: "
                        "all units of that side in the hex."
                    ),
                },
                "defender_filter": {
                    "type": "string",
                    "description": (
                        "Optional comma-separated name substrings restricting which "
                        "defender-side units are attacked. Default: all."
                    ),
                },
            },
            "required": ["hex_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "blind_hexes",
        "description": (
            "Compute BLIND HEXES (A6.4-.43, B10.23, B11.21): given a firer at a higher "
            "elevation looking over ONE obstacle - a terrain obstacle on a hill (woods, "
            "building), a bare hill Crest Line, or a cliff hexside - returns which of the "
            "hexes directly beyond it along the LOS are Blind Hexes, with a rule-cited "
            "step-by-step ledger. Use for any 'can X at level N see Y behind the hill/"
            "woods/cliff' or 'how many blind hexes' question once the elevations are "
            "known: read the firer level, the obstacle hex's hill level and terrain "
            "height, the range to the obstacle, and the level of each hex behind it, then "
            "call this instead of hand-deriving the A6.41/A6.42/A6.43 arithmetic. Use "
            "full levels only (half-level obstacles such as walls/hedges/rubble are "
            "ignored, A6.4). One obstacle per call; it does not model other obstacles, "
            "hindrances, or B10.2's no-recrossing rule."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "firer_level": {
                    "type": "integer",
                    "description": "Elevation level of the firer (0 = ground level).",
                },
                "obstacle_hex_level": {
                    "type": "integer",
                    "description": (
                        "Base hill level of the hex containing the obstacle. For a bare "
                        "Crest Line or cliff, the level on the HIGH side of the crest."
                    ),
                },
                "obstacle_height": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Full-level height of the terrain obstacle in that hex above its "
                        "base level: woods / one-story building 1, two-story building 2. "
                        "0 (default) = a bare hill Crest Line or cliff with no terrain "
                        "obstacle."
                    ),
                },
                "range_to_obstacle": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Range in hexes from the firer to the obstacle hex.",
                },
                "hexes_behind": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "integer"},
                    "description": (
                        "Elevation level of each hex directly beyond the obstacle along "
                        "the LOS, nearest first (e.g. [0, 0, 1])."
                    ),
                },
                "cliff": {
                    "type": "boolean",
                    "description": (
                        "The hexside the LOS exits the obstacle hex through is a cliff "
                        "hexside (B11). Default false."
                    ),
                },
            },
            "required": ["firer_level", "obstacle_hex_level", "range_to_obstacle",
                         "hexes_behind"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_section",
        "description": (
            "Fetch the EXACT text of one rulebook section by its ID (e.g. 'A12.14'), "
            "plus any official Q&A / errata entries about it. Use this to verify every "
            "rule section you cite, and to check cross-referenced sections for "
            "exceptions (EXC:), qualifiers, or NA clauses before answering. Prefer this "
            "over memory for any TEM/DRM/limit value."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Section ID, e.g. 'A12.14', 'B27.1', 'C13.31'.",
                },
                "include_subsections": {
                    "type": "boolean",
                    "description": "Also return the direct child sections (default false).",
                },
            },
            "required": ["section"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_rules",
        "description": (
            "Semantic search over the ASL rulebook and Q&A/errata for passages matching "
            "a query. Use when you need a rule but do not know its section number; then "
            "verify the found section with get_section."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, e.g. 'concealment loss sniper attack LOS'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max chunks to return (1-12, default 8).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ordnance_attack",
        "description": (
            "Resolve an ORDNANCE shot (Gun / vehicle MA / mortar / ordnance SW) from the "
            "SITUATION, per ASL Chapter C: Basic TH# from the C3 To Hit Table (red/black "
            "by nationality), C4 Gun & Ammo modifications, an itemized To Hit DRM ledger "
            "built from the C5/C6 cases you flag (CA change, AFPh, Bounding Fire, "
            "moving target, Point Blank, acquisition, target size, concealment, "
            "hindrance, TEM...), every 36-roll To Hit outcome (Critical Hit, Multiple "
            "Hits, Improbable Hits, hull vs turret, Hull Down), then the To Kill result "
            "(AP/APCR/APDS/HEAT/HE tables: Basic TK#, CH x2, rear +1, Case D range, "
            "minus the struck aspect's AF -> Burn/Elim/Immobilized/Shock/Possible Shock) "
            "or the IFT result vs personnel. Use whenever a question DESCRIBES an "
            "ordnance shot; if the Modified TH#, TH DRM and Final TK# are already "
            "known, use thtk_odds. Name the vehicles/Guns (target_vehicle, firer_vehicle, "
            "firer_gun, target_gun) and the tool reads AF, turret armor, Target Size, the "
            "MA or Gun caliber, ROF and B# from the eASLRB Chapter H listings; an ambiguous name returns the candidates to choose from. "
            "Explicit AF/gun values override the listing. NEVER hand-derive TH#/TK# or "
            "quote AF from memory — present the tool's ledgers and odds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "gun": {"type": "string",
                        "description": "Firing gun size as on the counter: '75L', '37*', '88LL', '57'. "
                                       "Omit when firer_vehicle is given (its MA is used)."},
                "firer_vehicle": {"type": "string",
                                  "description": "Firing vehicle's name, e.g. 'PzKpfw IVH', 'T-34 M43', "
                                                 "'Sherman VC(a)' — supplies gun, nationality, gun type, ROF, B#."},
                "target_vehicle": {"type": "string",
                                   "description": "Target vehicle's name, e.g. 'Panther', 'PzKpfw VG', "
                                                  "'KV-1E' — supplies AF for the facing, turret armor, Target Size."},
                "firer_gun": {"type": "string",
                              "description": "Firing Gun's name, e.g. '17pdr', 'PaK 40', 'M1 57mm AT Gun' — "
                                             "supplies caliber, nationality, mount (NT/T), ROF, B#."},
                "target_gun": {"type": "string",
                               "description": "Target Gun's name — supplies its Target Size (use the "
                                              "Infantry Target Type vs a Gun)."},
                "target_nationality": {"type": "string",
                                       "description": "Target's nationality, to pick between listings with the same name."},
                "nationality": {"type": "string",
                                "enum": thtk.get_options()["nationalities"],
                                "description": "Firer's nationality — picks red/black TH# and color-coded TK# rows."},
                "year": {"type": "integer",
                         "description": "Scenario year (Americans use red TH# before 1944)."},
                "th_color": {"type": "string", "enum": ["black", "red"],
                             "description": "Override the TH# color (captured guns, British Vehicle Note A, French vehicles...)."},
                "ammo": {"type": "string", "enum": thtk.get_options()["ammo_types"],
                         "description": "Default AP vs AFVs, HE vs unarmored (C2.21)."},
                "target_type": {"type": "string", "enum": thtk.get_options()["target_types"],
                                "description": "vehicle (a specific vehicle), infantry (HE vs an unarmored Location), area (mortars, SMOKE, HE)."},
                "range_hexes": {"type": "integer", "minimum": 0},
                "target": {
                    "type": "object",
                    "description": "What is fired on. Omit for personnel on the Infantry/Area Target Type.",
                    "properties": {
                        "kind": {"type": "string", "enum": ["armored", "unarmored", "personnel"]},
                        "facing": {"type": "string", "enum": ["front", "side", "rear"],
                                   "description": "Target Facing the shot strikes (rear = +1 Basic TK#)."},
                        "hull_af": {"type": "integer", "description": "Hull AF of that facing (counter)."},
                        "turret_af": {"type": "integer", "description": "Turret/upper-superstructure AF of that facing."},
                        "hull_down": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "basic_tk": {"type": "integer",
                             "description": "Override the table Basic TK# (Vehicle-Note specials, guns not on the table)."},
                "tem": {"type": "integer", "description": "Target's positive TEM (Case Q on Vehicle/Infantry types; Effects DRM on Area)."},
                "b_number": {"type": "integer", "description": "Gun's B# (default 12)."},
                "rof": {"type": "integer", "description": "Multiple ROF number (0 if none)."},
                "fp": {"type": "number", "description": "Override the IFT FP vs personnel."},
                "effects_drm": {"type": "integer", "description": "Other IFT Effects DRM vs personnel (FFMO/FFNAM, Air Burst)."},
                "gun_type": {"type": "string", "enum": thtk.get_options()["gun_types"],
                             "description": "T fast-traverse/mounted, ST slow-traverse turret, NT non-turreted, stabilized (Case C/C4)."},
                "ca_hexspines": {"type": "integer", "minimum": 0, "description": "CA/TCA hexspines changed for this shot (Case A)."},
                "firer_in_woods_building_rubble": {"type": "boolean"},
                "afph": {"type": "boolean", "description": "Firing in the AFPh without having moved (Case B)."},
                "bounding_fire": {"type": "boolean", "description": "Firer entered a new hex this Player Turn (Case C, includes Case B)."},
                "aim": {"type": "string", "enum": thtk.get_options()["aims"],
                        "description": "Bounding First Fire LOS: restricted (C1, 2.5-3 MP) / limited (C2, <=2 MP)."},
                "motion_firer": {"type": "boolean", "description": "Firing while in Motion/Non-Stopped (Case C4)."},
                "pinned": {"type": "boolean"},
                "intensive_fire": {"type": "boolean"},
                "captured": {"type": "boolean"},
                "buttoned_up": {"type": "boolean", "description": "BU CT AFV firing (Case I)."},
                "cx": {"type": "boolean"},
                "encircled": {"type": "boolean"},
                "leadership": {"type": "integer", "description": "Leader/armor-leader DRM (negative)."},
                "stun": {"type": "integer"},
                "spotted_fire": {"type": "boolean"},
                "overstack": {"type": "integer"},
                "target_moving": {"type": "string", "enum": thtk.get_options()["target_moving"],
                                  "description": "moving (J +2), j1 (DFF <=3 MP in LOS, +3), j2 (<=1 MP, +4)."},
                "ffnam": {"type": "boolean"},
                "ffmo": {"type": "boolean"},
                "concealed": {"type": "boolean"},
                "bore_sighted": {"type": "boolean"},
                "acquired": {"type": "integer", "minimum": -2, "maximum": 0},
                "hazardous_movement": {"type": "boolean"},
                "target_size": {"type": "integer", "minimum": -2, "maximum": 2,
                                "description": "Target Size DRM from the target counter (Case P)."},
                "hindrance": {"type": "integer", "minimum": 0},
                "other_drm": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"label": {"type": "string"}, "drm": {"type": "integer"}},
                              "required": ["label", "drm"], "additionalProperties": False},
                    "description": "Any other TH DRM not covered above.",
                },
            },
            "required": ["range_hexes"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "thtk_odds",
        "description": (
            "Ordnance To Hit / To Kill odds when the numbers are already known: the "
            "Modified TH#, the total TH DRM, and the Final TK# of the hit (or the IFT FP "
            "vs personnel). Enumerates the 36 TH rolls (Critical/Multiple/Improbable "
            "Hits, hull vs turret) and reads each hit against C7.7. If the question "
            "DESCRIBES the situation instead, use ordnance_attack."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "modified_th": {"type": "integer", "description": "Modified TH# (Basic + C4 mods)."},
                "drm": {"type": "integer", "description": "Total TH DRM (positive = harder)."},
                "target_type": {"type": "string", "enum": thtk.get_options()["target_types"]},
                "final_tk": {"type": "integer", "description": "Final TK# of a (hull) hit."},
                "turret_final_tk": {"type": "integer", "description": "Final TK# of a turret hit, if different."},
                "ch_final_tk": {"type": "integer", "description": "Final TK# of a Critical Hit."},
                "he": {"type": "boolean", "description": "Hit is HE (HE Shock/Immobilization on Final TK# +1)."},
                "unarmored": {"type": "boolean"},
                "hull_down": {"type": "boolean"},
                "multiple_hits": {"type": "boolean", "description": "15-40mm Gun eligible for Multiple Hits (C3.8)."},
                "ift_fp": {"type": "number", "description": "IFT FP vs personnel (instead of final_tk)."},
                "ch_ift_fp": {"type": "number", "description": "IFT FP of a Critical Hit vs personnel."},
                "effects_drm": {"type": "integer", "description": "IFT Effects DRM vs personnel."},
            },
            "required": ["modified_th"],
            "additionalProperties": False,
        },
    },
]

# Name groups: calculators vs deterministic-lookup tools. The service exposes
# these differently per path (see docs/agentic_retrieval_plan.md §3):
#   * OpenAI path: calc + get_section (hosted file_search covers search)
#   * OpenRouter path: calc + get_section + search_rules
LOOKUP_TOOL_NAMES = {"get_section", "search_rules"}
CALC_TOOL_NAMES = {"ift_odds", "ift_attack", "cc_attack", "resolve_attack", "resolve_cc",
                   "blind_hexes", "ordnance_attack", "thtk_odds"}


def calc_tool_schemas(chat: bool = False) -> List[Dict[str, Any]]:
    schemas = [s for s in TOOL_SCHEMAS if s["name"] in CALC_TOOL_NAMES]
    return to_chat_completions_tools(schemas) if chat else schemas


def lookup_tool_schemas(chat: bool = False, include_search: bool = True) -> List[Dict[str, Any]]:
    names = LOOKUP_TOOL_NAMES if include_search else (LOOKUP_TOOL_NAMES - {"search_rules"})
    schemas = [s for s in TOOL_SCHEMAS if s["name"] in names]
    return to_chat_completions_tools(schemas) if chat else schemas


def to_chat_completions_tools(
    schemas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Convert Responses-API tool schemas to Chat Completions function shape.

    The Responses API nests the function fields flat
    (`{"type": "function", "name", "description", "parameters"}`), while the
    Chat Completions / OpenRouter API nests them under a "function" key
    (`{"type": "function", "function": {"name", "description", "parameters"}}`).
    Deriving the Chat shape from `TOOL_SCHEMAS` keeps the two in lock-step so a
    tool added for the OpenAI path is automatically available on OpenRouter.
    """
    out: List[Dict[str, Any]] = []
    for s in (schemas if schemas is not None else TOOL_SCHEMAS):
        out.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return out


# Chat Completions function tools (for the OpenRouter agentic path).
TOOL_SCHEMAS_CHAT = to_chat_completions_tools()


# =============================================================================
# Dispatcher
# =============================================================================

TOOL_FUNCTIONS = {
    "ift_odds": ift_odds,
    "ift_attack": ift_attack,
    "cc_attack": cc_attack,
    "resolve_attack": resolve_attack,
    "resolve_cc": resolve_cc,
    "blind_hexes": blind_hexes,
    "ordnance_attack": ordnance_attack,
    "thtk_odds": thtk_odds,
    "get_section": get_section,
    "search_rules": search_rules,
}

# Tools that receive the server-side execution context (parsed .vsav state,
# retrieval client, ...) as a hidden `_context` kwarg. The model never sees or
# supplies it.
CONTEXT_TOOLS = {"resolve_attack", "resolve_cc", "search_rules"}


def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a tool by name with the given arguments.

    Args:
        tool_name: Registered tool name.
        arguments: Model-supplied arguments (already JSON-decoded).
        context: Optional server-side context (e.g. {"vsav_state": ...});
            injected only into CONTEXT_TOOLS, never model-controlled. An
            "on_tool_result" callable in it is called with (name, args, result)
            after every tool runs.

    Raises:
        ValueError: If the tool name is not recognized.
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {tool_name}")
    fn = TOOL_FUNCTIONS[tool_name]
    # Models occasionally carry a parameter from one tool into another (seen
    # live with ox-alpha: get_section's include_subsections passed to
    # resolve_attack). Drop anything the function doesn't accept rather than
    # failing the whole tool call with a TypeError.
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = None
    if params is not None and not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        dropped = [k for k in (arguments or {}) if k not in params]
        if dropped:
            logging.warning("%s: ignoring unknown argument(s) %s",
                            tool_name, dropped)
            arguments = {k: v for k, v in arguments.items() if k in params}
    if tool_name in CONTEXT_TOOLS:
        result = fn(**arguments, _context=context)
    else:
        result = fn(**arguments)
    # Callers can watch results (e.g. /api/ask turns an ordnance_attack result
    # into a calculator card). The hook never affects what the model gets.
    hook = (context or {}).get("on_tool_result")
    if hook:
        try:
            hook(tool_name, arguments, result)
        except Exception:
            logging.warning("on_tool_result hook failed for %s", tool_name, exc_info=True)
    return result
