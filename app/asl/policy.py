"""Policy and instruction building for ASL service."""
from typing import Optional


def build_instructions(
    base_instructions: str,
    question: str,
    force_web_search: bool = False
) -> str:
    """
    Build instructions for the LLM based on question type and options.
    
    Args:
        base_instructions: Base system instructions
        question: The question being asked
        force_web_search: If True, emphasize web search usage
        
    Returns:
        Complete instructions string
    """
    instructions = base_instructions
    
    # Add web search emphasis if requested
    if force_web_search:
        instructions += "\n\nIMPORTANT: For this question, prioritize web search to find current information, community discussions, or recent clarifications."
    
    return instructions

