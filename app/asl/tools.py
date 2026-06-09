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
from typing import Dict, Any, Optional

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
