"""Post-processing utilities for ASL service responses."""
import json
import time
from typing import Dict, Any, Optional, List


def extract_response_text(response) -> str:
    """Extract text from response object."""
    if hasattr(response, 'output_text') and response.output_text:
        return response.output_text
    elif hasattr(response, 'output') and response.output:
        if isinstance(response.output, str):
            return response.output
        elif isinstance(response.output, dict) and 'text' in response.output:
            return response.output['text']
    
    return "No response content found"


def format_structured_answer(structured_data: dict) -> str:
    """
    Convert structured JSON answer into human-readable format.
    
    Args:
        structured_data: Parsed JSON response
        
    Returns:
        Formatted string answer
    """
    parts = []
    
    # Question analysis
    if "question_analysis" in structured_data:
        qa = structured_data["question_analysis"]
        parts.append(f"Question Type: {qa.get('question_type', 'unknown')}")
    
    # Applicable rules
    if "applicable_rules" in structured_data:
        applicable = [r for r in structured_data["applicable_rules"] if r.get("applies")]
        if applicable:
            parts.append("\nApplicable Rules:")
            for rule in applicable:
                parts.append(f"  • {rule['section']}: {rule['rule_text']}")
    
    # Modifiers
    if "modifiers" in structured_data and structured_data["modifiers"]:
        parts.append("\nModifiers:")
        for mod in structured_data["modifiers"]:
            sign = "+" if mod["value"] > 0 else ""
            parts.append(f"  • {mod['type']}: {sign}{mod['value']} ({mod['source']}) - {mod['description']}")
    
    # Calculation steps
    if "calculation_steps" in structured_data:
        parts.append("\nCalculation:")
        for step in structured_data["calculation_steps"]:
            parts.append(f"  Step {step['step_number']}: {step['operation']} → {step['intermediate_value']}")
            parts.append(f"    ({step['explanation']})")
    
    # Final answer
    if "final_answer" in structured_data:
        fa = structured_data["final_answer"]
        parts.append(f"\n**Final Answer: {fa['value']} {fa['unit']}**")
        parts.append(f"Confidence: {fa['confidence']}")
        if fa.get("verification_check"):
            parts.append(f"Verification: {fa['verification_check']}")
    
    # Human readable summary
    if "human_readable_answer" in structured_data:
        parts.append(f"\n{structured_data['human_readable_answer']}")
    
    return "\n".join(parts)


def parse_structured_json(response_text: str) -> dict:
    """
    Parse JSON from response text, handling markdown code blocks.
    
    Args:
        response_text: Response text that may contain JSON in code blocks
        
    Returns:
        Parsed JSON dict
        
    Raises:
        json.JSONDecodeError: If JSON parsing fails
    """
    json_text = response_text.strip()
    if json_text.startswith("```json"):
        json_text = json_text.split("```json")[1].split("```")[0].strip()
    elif json_text.startswith("```"):
        json_text = json_text.split("```")[1].split("```")[0].strip()
    
    return json.loads(json_text)


def compute_timing_metrics(
    api_call_start_time: float,
    first_event_time: Optional[float] = None,
    file_search_complete_time: Optional[float] = None,
    first_delta_time: Optional[float] = None,
    stream_end_time: Optional[float] = None
) -> Dict[str, Any]:
    """
    Compute timing metrics from event timestamps.
    
    Args:
        api_call_start_time: When API call started
        first_event_time: When first event was received
        file_search_complete_time: When file search completed
        first_delta_time: When first text delta was received
        stream_end_time: When streaming ended
        
    Returns:
        Dictionary with timing metrics
    """
    first_event_ms = (first_event_time - api_call_start_time) * 1000 if first_event_time else None
    file_search_complete_ms = (file_search_complete_time - api_call_start_time) * 1000 if file_search_complete_time else None
    first_token_ms = (first_delta_time - api_call_start_time) * 1000 if first_delta_time else None
    total_streaming_time_ms = (stream_end_time - api_call_start_time) * 1000 if stream_end_time else None
    generation_time_ms = (stream_end_time - file_search_complete_time) * 1000 if file_search_complete_time else None
    rag_time_ms = file_search_complete_ms if file_search_complete_ms else None
    
    return {
        "api_call_start": api_call_start_time,
        "first_event_ms": first_event_ms,
        "file_search_complete_ms": file_search_complete_ms,
        "first_token_ms": first_token_ms,
        "stream_end_ms": total_streaming_time_ms,
        "total_ms": total_streaming_time_ms,
        "rag_time_ms": rag_time_ms,
        "generation_time_ms": generation_time_ms
    }

