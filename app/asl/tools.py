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
    phase: str = "prep",
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
            phase=phase,
            firing_unit_filter=firing_unit_filter,
        )
    except ValueError as e:
        return {"error": str(e)}
    logging.info(
        "🎯 resolve_attack(%s -> %s, phase=%s) -> %s FP, col %s, drm %s",
        firing_hex, target_hex, phase,
        result.get("total_fp"), result.get("column"), result.get("drm"),
    )
    return result


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
            "IFT odds, plus explicit assumptions (LOS assumed clear, intervening "
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
                    "description": "Hex being attacked, e.g. '57-H8'.",
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
]


# =============================================================================
# Dispatcher
# =============================================================================

TOOL_FUNCTIONS = {
    "ift_odds": ift_odds,
    "ift_attack": ift_attack,
    "cc_attack": cc_attack,
    "resolve_attack": resolve_attack,
    "resolve_cc": resolve_cc,
}

# Tools that receive the server-side execution context (parsed .vsav state,
# ...) as a hidden `_context` kwarg. The model never sees or supplies it.
CONTEXT_TOOLS = {"resolve_attack", "resolve_cc"}


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
            injected only into CONTEXT_TOOLS, never model-controlled.

    Raises:
        ValueError: If the tool name is not recognized.
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {tool_name}")
    if tool_name in CONTEXT_TOOLS:
        return TOOL_FUNCTIONS[tool_name](**arguments, _context=context)
    return TOOL_FUNCTIONS[tool_name](**arguments)
