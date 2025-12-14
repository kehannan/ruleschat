"""Policy and instruction building for ASL service."""
import re
from typing import Optional


def is_calculation_question(question: str) -> bool:
    """
    Determine if a question requires calculation.
    
    Args:
        question: The question text
        
    Returns:
        True if question appears to require calculation
    """
    question_lower = question.lower()
    
    # Keywords that suggest calculation
    calculation_keywords = [
        "calculate", "compute", "what is", "how much", "how many",
        "drm", "dice roll modifier", "modifier", "modifiers",
        "residual", "residual fp", "residual firepower",
        "column shift", "column shifts", "shift",
        "total", "sum", "add", "subtract", "multiply", "divide",
        "final", "result", "answer is", "equals"
    ]
    
    # Check for calculation keywords
    for keyword in calculation_keywords:
        if keyword in question_lower:
            return True
    
    # Check for patterns like "X + Y" or "X - Y" or "X / Y"
    if re.search(r'\d+\s*[+\-*/]\s*\d+', question):
        return True
    
    # Check for "what is" followed by numbers or calculations
    if re.search(r'what is.*\d+', question_lower):
        return True
    
    return False


def _get_structured_output_schema() -> dict:
    """Get JSON schema for structured output."""
    return {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The final answer to the question"
            },
            "calculation_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "Description of this calculation step"},
                        "value": {"type": "number", "description": "Numeric value for this step"},
                        "explanation": {"type": "string", "description": "Explanation of this step"}
                    },
                    "required": ["step", "value", "explanation"]
                },
                "description": "Step-by-step calculation breakdown"
            },
            "rule_references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ASL rule section references (e.g., A4.1, B18.2)"
            }
        },
        "required": ["answer", "calculation_steps", "rule_references"]
    }


def build_instructions(
    base_instructions: str,
    question: str,
    force_web_search: bool = False,
    use_structured_output: bool = False
) -> str:
    """
    Build instructions for the LLM based on question type and options.
    
    Args:
        base_instructions: Base system instructions
        question: The question being asked
        force_web_search: If True, emphasize web search usage
        use_structured_output: If True, add JSON schema requirements
        
    Returns:
        Complete instructions string
    """
    instructions = base_instructions
    
    # Add web search emphasis if requested
    if force_web_search:
        instructions += "\n\nIMPORTANT: For this question, prioritize web search to find current information, community discussions, or recent clarifications."
    
    # Add structured output requirements if enabled
    if use_structured_output:
        schema = _get_structured_output_schema()
        instructions += f"""

STRUCTURED OUTPUT REQUIRED:
You MUST respond with a valid JSON object matching this schema:
{{
  "answer": "Your final answer as a string",
  "calculation_steps": [
    {{
      "step": "Description of step",
      "value": <numeric_value>,
      "explanation": "Explanation"
    }}
  ],
  "rule_references": ["A4.1", "B18.2", ...]
}}

IMPORTANT:
- The JSON must be valid and parseable
- Include ALL calculation steps with intermediate values
- List ALL relevant rule section references
- Do not include any text outside the JSON object"""
    
    return instructions

