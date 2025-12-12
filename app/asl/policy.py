"""Policy and instruction building for ASL service."""
import json
from typing import Optional


def is_calculation_question(question: str) -> bool:
    """Detect if a question involves calculations or DRMs."""
    calculation_keywords = [
        'drm', 'dice roll modifier', 'modifier', 'modifiers',
        'calculate', 'calculation', 'what is the', 'final drm',
        'total drm', 'residual fp', 'residual fire',
        'fire power', 'sum', 'total', 'result',
        'how much', 'how many', 'what number',
        'dr result', 'dice roll', 'roll of',
        'elevation advantage', 'level advantage',
        'column shift', 'ift column'
    ]
    question_lower = question.lower()
    return any(keyword in question_lower for keyword in calculation_keywords)


def get_structured_output_schema() -> dict:
    """
    Get JSON schema for structured calculation output.
    
    Returns:
        JSON schema dict for OpenAI structured output
    """
    return {
        "type": "object",
        "properties": {
            "question_analysis": {
                "type": "object",
                "properties": {
                    "question_type": {
                        "type": "string",
                        "description": "Type of question (e.g., 'drm_calculation', 'blind_hexes', 'residual_fp', 'rule_lookup')"
                    },
                    "key_elements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key elements from the question (units, terrain, ranges, etc.)"
                    }
                },
                "required": ["question_type", "key_elements"]
            },
            "applicable_rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "description": "Rule section (e.g., A8.26)"},
                        "rule_text": {"type": "string", "description": "Relevant rule text"},
                        "applies": {"type": "boolean", "description": "Whether this rule applies to this situation"}
                    },
                    "required": ["section", "rule_text", "applies"]
                },
                "description": "All rules that could potentially apply"
            },
            "modifiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Modifier type (terrain, elevation, leadership, etc.)"},
                        "value": {"type": "number", "description": "Numeric value of modifier"},
                        "source": {"type": "string", "description": "Rule section for this modifier"},
                        "description": {"type": "string", "description": "Brief explanation"}
                    },
                    "required": ["type", "value", "source", "description"]
                },
                "description": "All applicable modifiers"
            },
            "calculation_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "integer"},
                        "operation": {"type": "string", "description": "Math operation or rule application"},
                        "intermediate_value": {"type": "string", "description": "Value after this step"},
                        "explanation": {"type": "string", "description": "Why this step was performed"}
                    },
                    "required": ["step_number", "operation", "intermediate_value", "explanation"]
                },
                "description": "Step-by-step calculation"
            },
            "final_answer": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "description": "The final numeric/text answer"},
                    "unit": {"type": "string", "description": "Unit of measurement (FP, DRM, hexes, etc.)"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"], "description": "Confidence in answer"},
                    "verification_check": {"type": "string", "description": "Double-check of the answer"}
                },
                "required": ["value", "unit", "confidence", "verification_check"]
            },
            "human_readable_answer": {
                "type": "string",
                "description": "Complete answer in natural language with section references"
            }
        },
        "required": ["question_analysis", "applicable_rules", "modifiers", "calculation_steps", "final_answer", "human_readable_answer"]
    }


def build_instructions(
    base_instructions: str,
    question: str,
    force_web_search: bool = False,
    use_structured_output: bool = False
) -> str:
    """
    Build instructions with optional enhancements.
    
    Args:
        base_instructions: Base system instructions
        question: The question being asked
        force_web_search: If True, emphasizes web search usage
        use_structured_output: If True, adds structured output schema
        
    Returns:
        Complete instructions string
    """
    instructions = base_instructions
    
    # Add web search emphasis if requested
    if force_web_search:
        instructions += "\n\nIMPORTANT: The user has requested web search. You MUST use web_search to find current information, community discussions, and recent clarifications. Also use file_search to reference the rulebook. Use both tools together to provide a comprehensive answer."
    
    # Add structured output or calculation scaffolding
    if use_structured_output and is_calculation_question(question):
        # Force JSON structured output
        schema = get_structured_output_schema()
        instructions += f"""

STRUCTURED OUTPUT REQUIRED - Respond with valid JSON matching this exact schema:

{json.dumps(schema, indent=2)}

Fill in ALL required fields. Do not skip any section. Be thorough and complete.

For modifiers, check:
- Terrain (grain, smoke, buildings, walls, etc.)
- Elevation (LOS advantage, blind hexes calculation)
- Range effects
- Leadership modifiers
- Unit status
- Special rules and exceptions

For calculations, show EVERY step including:
- Division operations (e.g., FP ÷ 2 for Residual FP)
- Column shifts (show each shift: 16 → 12 → 8)
- Addition/subtraction of modifiers

Your entire response must be valid JSON.
"""
    elif is_calculation_question(question):
        # Regular chain-of-thought
        instructions += """

CALCULATION QUESTION DETECTED - Use this structured approach:
1. First, list ALL applicable modifiers/values with their sources (cite section numbers)
2. Show the calculation step-by-step with intermediate values
3. Double-check your math before stating the final answer
4. Verify the direction of modifiers (+ or -) and perspective (who is attacking/defending)
5. State your final answer clearly

Remember: 
- Don't skip steps in calculations
- Check if the question asks about attacking FROM or being attacked IN a location
- For DRMs: list each modifier, then sum them (show the addition)
- For column shifts: show each shift step (e.g., 8 → 6 → 4)
"""
    
    return instructions

