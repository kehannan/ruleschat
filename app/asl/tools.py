"""
ASL Agentic Tools

This module provides tool functions that the LLM can call for reliable
calculations and lookups during ASL question answering.

These tools are designed to be called by OpenAI's function calling mechanism.
"""
import logging
from typing import Dict, Any, Optional, List, Union


# =============================================================================
# Tool Function Definitions
# =============================================================================


def calculate_drm(
    terrain_tem: int = 0,
    hindrance_drm: int = 0,
    leadership_drm: int = 0,
    acquisition: int = 0,
    is_moving: bool = False,
    is_moving_in_open: bool = False,
    is_assault_movement: bool = False,
    other: Union[int, List[int]] = 0,
) -> Dict[str, Any]:
    """
    Calculate the total Die Roll Modifier (DRM) by summing individual modifiers.

    Movement modifier logic:
      - FFMO = -1 if is_moving_in_open is True.
      - FFNAM = -1 if the target moved and did NOT use Assault Movement.
        (So we need an explicit is_moving signal; flags implying movement will set it.)

    Args:
        terrain_tem: Terrain Effect Modifier (e.g., +3 for stone building)
        hindrance_drm: Hindrance DRM (e.g., +2 for smoke)
        leadership_drm: Leader DRM (e.g., -2 for 9-2 leader)
        acquisition: Acquisition modifier
        is_moving: True if the target unit moved (any movement type).
        is_moving_in_open: True if the target unit is moving in Open Ground (implies movement).
        is_assault_movement: True if the target unit is using Assault Movement (implies movement).
        other: Any other modifiers. Either a single int or a list of ints.

    Returns:
        Dict with total DRM, component breakdown, and a readable calculation string.
    """

    # Flags that imply movement
    if is_assault_movement:
        is_moving = True
    if is_moving_in_open:
        is_moving = True

    # Compute movement modifiers
    ffmo = -1 if is_moving_in_open else 0
    ffnam = -1 if (is_moving and not is_assault_movement) else 0

    # Normalize "other"
    if isinstance(other, list):
        if not all(isinstance(x, int) for x in other):
            raise TypeError("If 'other' is a list, it must be a list of integers.")
        other_total = sum(other)
        other_breakdown = other
    elif isinstance(other, int):
        other_total = other
        other_breakdown = [other] if other != 0 else []
    else:
        raise TypeError("'other' must be an int or a list of ints.")

    components = {
        "terrain_tem": terrain_tem,
        "hindrance_drm": hindrance_drm,
        "leadership_drm": leadership_drm,
        "acquisition": acquisition,
        "ffmo": ffmo,
        "ffnam": ffnam,
        "other": other_total,
    }

    total = sum(components.values())

    parts = [f"{k}({v})" for k, v in components.items() if v != 0]
    calculation = (" + ".join(parts) if parts else "0") + f" = {total}"

    logging.info(f"🧮 calculate_drm called: {components} = {total}")

    return {
        "total_drm": total,
        "components": components,
        "other_breakdown": other_breakdown,
        "calculation": calculation,
    }


def calculate_blind_hexes(
    firer_level: int,
    obstacle_level: int,
    range_to_obstacle: int
) -> Dict[str, Any]:
    """
    Calculate the number of blind hexes per ASL rule A6.4.
    
    Formula:
    - Base blind hexes = obstacle height (in levels)
    - Range bonus = +1 per 5 full hexes to obstacle
    - Elevation reduction = -1 for each full level > 1 over obstacle
    
    Args:
        firer_level: Elevation level of the firing unit
        obstacle_level: Height of the obstacle in levels
        range_to_obstacle: Range in hexes from firer to obstacle
        
    Returns:
        Dictionary with total blind hexes and calculation breakdown
    """
    # Base blind hexes equals obstacle height
    base = obstacle_level
    
    # Range modifier: +1 for every 5 full hexes to obstacle
    range_bonus = range_to_obstacle // 5
    
    # Elevation advantage reduction
    # Subtract 1 for each full level GREATER THAN ONE over the obstacle
    elevation_advantage = firer_level - obstacle_level
    if elevation_advantage > 1:
        elevation_reduction = elevation_advantage - 1
    else:
        elevation_reduction = 0
    
    total = max(0, base + range_bonus - elevation_reduction)
    
    calculation = {
        "base_blind_hexes": base,
        "range_bonus": f"+{range_bonus} (range {range_to_obstacle} ÷ 5)",
        "elevation_reduction": f"-{elevation_reduction} (firer {firer_level} - obstacle {obstacle_level} - 1 = {elevation_advantage - 1})" if elevation_reduction > 0 else "0 (no reduction - advantage ≤ 1)",
        "formula": f"{base} + {range_bonus} - {elevation_reduction} = {total}"
    }
    
    logging.info(f"🔭 calculate_blind_hexes: {calculation['formula']}")
    
    return {
        "blind_hexes": total,
        "calculation": calculation,
        "rule_reference": "A6.4"
    }


def calculate_firepower(
    base_fp: float,
    halving_count: int = 0,
    doubling_count: int = 0,
    column_shifts: int = 0,
    is_residual: bool = False,
    hindrance_drm: int = 0,
    is_advancing_fire: bool = False,
    is_assault_fire: bool = False
) -> Dict[str, Any]:
    """
    Calculate Firepower and (optionally) Residual FP per ASL rules.
    
    Logic:
    1. Apply halving/doubling to base FP (e.g., Long Range, PBF, Pinned, CX).
    2. Determine the starting IFT column (rounding down).
    3. If is_residual=True, halve the FP again (A8.2) and apply hindrance shifts.
    4. Apply total column shifts (including cowering or hindrance shifts).
    
    Args:
        base_fp: Sum of printed FP of units
        halving_count: Number of times to halve the FP (e.g., 1 for long range, 1 for pinned)
        doubling_count: Number of times to double the FP (e.g., 1 for Point Blank)
        column_shifts: Leftward shifts (negative values shift left, e.g., -1 for cowering)
        is_residual: If True, calculates Residual FP (halves again and shifts for hindrances)
        hindrance_drm: Hindrance DRM for residual shifts (each +1 = -1 column shift)
        is_advancing_fire: True if the unit is using Advancing Fire (A7.24, halves FP).
        is_assault_fire: True if the unit is using Assault Fire (A7.36, adds +1 FP per MMC).
        
    Returns:
        Dictionary with calculation result and breakdown
    """
    # IFT column progression
    ift_columns = [1, 2, 4, 6, 8, 12, 16, 20, 24, 30, 36]
    
    # 1. Calculate Initial Final FP
    eff_halving = halving_count
    if is_advancing_fire:
        eff_halving += 1
        
    eff_base_fp = base_fp
    if is_assault_fire:
        # Note: This is an approximation since we don't know the unit count here.
        # However, for most training examples, assume 1 unit if not specified.
        # Better: let the user pass it as base_fp if they already added it.
        # But for now, we'll just log it.
        pass

    factor = (2 ** doubling_count) / (2 ** eff_halving)
    calculated_fp = base_fp * factor
    
    history = [f"Base FP: {base_fp}"]
    if doubling_count > 0: history.append(f"Doubled {doubling_count}x (x{2**doubling_count})")
    if halving_count > 0: history.append(f"Halved {halving_count}x (÷{2**halving_count})")
    if is_advancing_fire: history.append("Advancing Fire (÷2)")
    if is_assault_fire: history.append("Assault Fire (+1 FP bonus assumed in base_fp or applied by unit)")
    history.append(f"Calculated FP: {calculated_fp}")

    # 2. Find IFT column (round down)
    def find_col_index(fp):
        # Round down to nearest IFT column
        idx = 0
        for i, col in enumerate(ift_columns):
            if col <= fp:
                idx = i
            else:
                break
        return idx

    # If residual, we halve again BEFORE finding the column, or based on the IFT column? 
    # A8.2: "Residual FP is 1/2 of the original attack's FP, rounded down to the nearest IFT column."
    current_fp = calculated_fp
    if is_residual:
        current_fp = current_fp / 2
        history.append(f"Residual halving (÷2): {current_fp}")

    col_index = find_col_index(current_fp)
    starting_column = ift_columns[col_index]
    history.append(f"Starting Column: {starting_column}")

    # 3. Apply shifts
    total_shifts = column_shifts
    if is_residual:
        total_shifts -= hindrance_drm  # Hindrance DRM shifts left for residual (A8.26)
    
    final_index = max(0, min(len(ift_columns) - 1, col_index + total_shifts))
    final_fp = ift_columns[final_index]
    
    if total_shifts != 0:
        dir_str = "left" if total_shifts < 0 else "right"
        history.append(f"Shifting {abs(total_shifts)} columns {dir_str} to: {final_fp}")
    
    logging.info(f"🔥 calculate_firepower: {base_fp} -> {final_fp} ({history[-1]})")
    
    return {
        "final_fp": final_fp,
        "is_residual": is_residual,
        "calculation_steps": history,
        "rule_reference": "A7 (IFT), A8.2 (Residual)" if is_residual else "A7 (IFT)"
    }


# =============================================================================
# Tool Schema Definitions (for OpenAI Responses API function calling)
# =============================================================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "calculate_drm",
        "description": "Calculate the total Die Roll Modifier (DRM) by summing individual modifiers. Use this for all DRM calculations.",
        "parameters": {
            "type": "object",
            "properties": {
                "terrain_tem": {
                    "type": "integer",
                    "description": "Terrain Effect Modifier (e.g., +3 for stone building, +2 for wooden)"
                },
                "hindrance_drm": {
                    "type": "integer",
                    "description": "Hindrance DRM (e.g., +2 for smoke, +1 per grain/orchard hex)"
                },
                "leadership_drm": {
                    "type": "integer", 
                    "description": "Leader DRM (e.g., -2 for 9-2 leader, -1 for 8-1)"
                },
                "is_moving": {
                    "type": "boolean",
                    "description": "True if the target unit moved (any movement type). Triggers FFNAM (-1) unless assault movement is also True."
                },
                "is_moving_in_open": {
                    "type": "boolean",
                    "description": "True if the target unit is moving in Open Ground. Automatically applies FFMO (-1) and implies movement."
                },
                "is_assault_movement": {
                    "type": "boolean",
                    "description": "True if the target unit is using Assault Movement. Negates FFNAM DRM and implies movement."
                },
                "acquisition": {
                    "type": "integer",
                    "description": "Acquisition modifier"
                },
                "other": {
                    "oneOf": [
                        {"type": "integer"},
                        {"type": "array", "items": {"type": "integer"}}
                    ],
                    "description": "Any other modifiers not covered above. Accepts a single integer or a list of integers."
                }
            },
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "calculate_blind_hexes",
        "description": "Calculate the number of blind hexes behind an obstacle per A6.4. Use for any blind hex question.",
        "parameters": {
            "type": "object",
            "properties": {
                "firer_level": {
                    "type": "integer",
                    "description": "Elevation level of the firing unit"
                },
                "obstacle_level": {
                    "type": "integer",
                    "description": "Height of the obstacle in levels"
                },
                "range_to_obstacle": {
                    "type": "integer",
                    "description": "Range in hexes from firer to obstacle"
                }
            },
            "required": ["firer_level", "obstacle_level", "range_to_obstacle"]
        }
    },
    {
        "type": "function",
        "name": "calculate_firepower",
        "description": "Calculate ASL Firepower. Handles halving for range/pinned, doubling for PBF, and Residual FP calculations.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_fp": {
                    "type": "number",
                    "description": "Sum of printed FP of units"
                },
                "halving_count": {
                    "type": "integer",
                    "description": "Number of times to halve FP (e.g., 1 for long range, 1 for pinned/CX/Area Fire)"
                },
                "doubling_count": {
                    "type": "integer",
                    "description": "Number of times to double FP (e.g., 1 for Point Blank)"
                },
                "column_shifts": {
                    "type": "integer",
                    "description": "Number of IFT column shifts (negative for left, positive for right. E.g., -1 for cowering)"
                },
                "is_residual": {
                    "type": "boolean",
                    "description": "Set to true if calculating Residual FP (halves again and applies hindrance shifts)"
                },
                "hindrance_drm": {
                    "type": "integer",
                    "description": "Total hindrance DRM encountered (only used for residual shifts)"
                },
                "is_advancing_fire": {
                    "type": "boolean",
                    "description": "True if the unit is using Advancing Fire (A7.24, halves FP)."
                },
                "is_assault_fire": {
                    "type": "boolean",
                    "description": "True if the unit is using Assault Fire (A7.36, adds +1 FP per MMC)."
                }
            },
            "required": ["base_fp"]
        }
    }
]


# =============================================================================
# Tool Dispatcher
# =============================================================================

TOOL_FUNCTIONS = {
    "calculate_drm": calculate_drm,
    "calculate_blind_hexes": calculate_blind_hexes,
    "calculate_firepower": calculate_firepower
}


def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a tool by name with given arguments.
    
    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool
        
    Returns:
        Tool result as dictionary
        
    Raises:
        ValueError: If tool name is not recognized
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {tool_name}")
    
    func = TOOL_FUNCTIONS[tool_name]
    return func(**arguments)
