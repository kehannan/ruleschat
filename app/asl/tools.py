"""
ASL Agentic Tools

Thin wrappers exposing the deterministic UI calculators — the Infantry Fire
Table odds engine (`app.asl.ift`) and the To Hit / To Kill odds engine
(`app.asl.thtk`) — as functions the LLM can call via OpenAI's function calling.

The same engines power the `/ift` and `/thtk` pages, so an agentic answer and
the standalone tool always agree. Tool schemas pull their enums live from the
engines, so they can never drift from the underlying tables.
"""
import logging
from typing import Dict, Any, List, Optional

from app.asl import ift, thtk


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
    )
    result.pop("cells", None)  # UI-only heatmap data; not useful to the model
    logging.info(
        "🧮 ift_attack(%d unit(s), afph=%s, area=%s) -> %s FP, col %s, drm %s, cowering %s",
        len(units), afph, area_fire_halvings,
        result.get("total_fp"), result.get("column"), result.get("drm"),
        result.get("cowering"),
    )
    return result


def thtk_odds(
    target_type: str,
    range: int,
    weapon_type: str,
    ammo: str,
    mm: int,
    nationality: str = "",
    hit_drm: int = 0,
) -> Dict[str, Any]:
    """
    To Hit + To Kill numbers and probabilities for one ordnance attack (Chapter C).

    Target Armor is NOT applied on the To Kill side — the raw TK# is returned, so
    the caller subtracts armor themselves (each armor point = -1 to the TK#).

    Args:
        target_type: One of `thtk.get_options()["target_types"]`.
        range: Range to target in hexes (>= 0).
        weapon_type: Barrel class, one of `thtk.get_options()["weapon_types"]`.
        ammo: Ammo type, one of `thtk.get_options()["ammo_types"]`.
        mm: Gun caliber in millimeters (> 0).
        nationality: One of `thtk.get_options()["nationalities"]`, or "" for none
                     (German optics use a higher To Hit column).
        hit_drm: Hit Determination DRM (positive = harder to hit).
    """
    result = thtk.compute(
        target_type=target_type,
        rng=range,
        weapon_type=weapon_type,
        ammo=ammo,
        mm=mm,
        nationality=nationality,
        hit_drm=hit_drm,
    )
    logging.info(
        "🎯 thtk_odds(target=%s, rng=%s, %s, %s, %smm) -> TH#%s / TK#%s",
        target_type, range, weapon_type, ammo, mm,
        result["to_hit"]["final_th"], result["to_kill"]["final_tk"],
    )
    return result


# =============================================================================
# Tool Schemas (OpenAI Responses API function calling) — enums pulled live
# =============================================================================

_thtk_opts = thtk.get_options()

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
                                "description": "Firing at long range — FP halved (A7.22).",
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
        "name": "thtk_odds",
        "description": (
            "Compute To Hit and To Kill numbers and probabilities for an ordnance "
            "(Gun) attack per ASL Chapter C. Use for any 'chance to hit / chance to "
            "kill' question for a Gun firing at a target at a given range. Target Armor "
            "is NOT applied — the raw To Kill number is returned for the caller to "
            "adjust by armor (each armor point = -1 to the TK#)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "enum": _thtk_opts["target_types"],
                    "description": "Target type.",
                },
                "range": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Range to target in hexes.",
                },
                "weapon_type": {
                    "type": "string",
                    "enum": _thtk_opts["weapon_types"],
                    "description": "Gun barrel class.",
                },
                "ammo": {
                    "type": "string",
                    "enum": _thtk_opts["ammo_types"],
                    "description": "Ammunition type.",
                },
                "mm": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Gun caliber in millimeters.",
                },
                "nationality": {
                    "type": "string",
                    "enum": [""] + _thtk_opts["nationalities"],
                    "description": "Firer nationality (German optics use a higher column). '' for none.",
                },
                "hit_drm": {
                    "type": "integer",
                    "description": "Hit Determination DRM (positive = harder to hit).",
                },
            },
            "required": ["target_type", "range", "weapon_type", "ammo", "mm"],
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
    "thtk_odds": thtk_odds,
}


def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool by name with the given arguments.

    Raises:
        ValueError: If the tool name is not recognized.
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {tool_name}")
    return TOOL_FUNCTIONS[tool_name](**arguments)
