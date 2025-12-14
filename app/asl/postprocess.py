"""Post-processing utilities for ASL service responses."""
import json
import re
import logging
from typing import Optional, Dict, Any


def extract_response_text(response) -> str:
    """
    Extract text from a Responses API response object.
    
    Args:
        response: Response object from OpenAI Responses API
        
    Returns:
        Extracted text content
    """
    if hasattr(response, 'output_text'):
        return response.output_text or ""
    elif hasattr(response, 'text'):
        return response.text or ""
    elif isinstance(response, str):
        return response
    else:
        logging.warning("⚠️ Could not extract text from response object")
        return str(response)


def parse_structured_json(response_text: str) -> Dict[str, Any]:
    """
    Parse JSON from response text, handling markdown code blocks.
    
    Args:
        response_text: Response text that may contain JSON in code blocks
        
    Returns:
        Parsed JSON as dictionary
        
    Raises:
        json.JSONDecodeError: If JSON cannot be parsed
    """
    # Try to extract JSON from markdown code blocks
    json_match = None
    
    # Look for ```json ... ``` blocks
    json_block_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    if json_block_match:
        json_match = json_block_match.group(1)
    else:
        # Look for ``` ... ``` blocks
        code_block_match = re.search(r'```\s*(.*?)\s*```', response_text, re.DOTALL)
        if code_block_match:
            json_match = code_block_match.group(1)
        else:
            # Look for JSON object directly
            json_obj_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_obj_match:
                json_match = json_obj_match.group(0)
    
    if json_match:
        return json.loads(json_match.strip())
    else:
        # Try parsing the entire response as JSON
        return json.loads(response_text.strip())


def format_structured_answer(structured_data: Dict[str, Any]) -> str:
    """
    Format structured JSON answer into readable text.
    
    Args:
        structured_data: Parsed JSON data with answer, calculation_steps, rule_references
        
    Returns:
        Formatted answer text
    """
    answer = structured_data.get("answer", "")
    calculation_steps = structured_data.get("calculation_steps", [])
    rule_references = structured_data.get("rule_references", [])
    
    formatted = []
    
    # Add answer
    if answer:
        formatted.append(answer)
    
    # Add calculation steps if present
    if calculation_steps:
        formatted.append("\nCalculation Steps:")
        for i, step in enumerate(calculation_steps, 1):
            step_desc = step.get("step", "")
            step_value = step.get("value", "")
            step_explanation = step.get("explanation", "")
            
            step_text = f"{i}. {step_desc}"
            if step_value is not None:
                step_text += f" = {step_value}"
            if step_explanation:
                step_text += f" ({step_explanation})"
            
            formatted.append(step_text)
    
    # Add rule references
    if rule_references:
        refs_str = ", ".join(rule_references)
        formatted.append(f"\nRule References: {refs_str}")
    
    return "\n".join(formatted)


def compute_timing_metrics(
    api_call_start_time: float,
    first_event_time: Optional[float],
    file_search_complete_time: Optional[float],
    first_delta_time: Optional[float],
    stream_end_time: Optional[float]
) -> Dict[str, Any]:
    """
    Compute timing metrics from event timestamps.
    
    Args:
        api_call_start_time: When the API call was initiated
        first_event_time: When the first event was received
        file_search_complete_time: When file search completed
        first_delta_time: When first text delta was received (TTFT)
        stream_end_time: When streaming ended
        
    Returns:
        Dictionary with timing metrics in milliseconds
    """
    metrics = {}
    
    if first_event_time:
        metrics["first_event_time_ms"] = (first_event_time - api_call_start_time) * 1000
    
    if file_search_complete_time:
        metrics["file_search_time_ms"] = (file_search_complete_time - api_call_start_time) * 1000
    
    if first_delta_time:
        metrics["ttft_ms"] = (first_delta_time - api_call_start_time) * 1000
    
    if stream_end_time:
        metrics["total_time_ms"] = (stream_end_time - api_call_start_time) * 1000
    
    # Compute derived metrics
    if file_search_complete_time and first_delta_time:
        metrics["generation_time_ms"] = (first_delta_time - file_search_complete_time) * 1000
    
    if file_search_complete_time and stream_end_time:
        metrics["post_rag_generation_time_ms"] = (stream_end_time - file_search_complete_time) * 1000
    
    return metrics

