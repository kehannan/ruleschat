"""Post-processing utilities for ASL service responses."""
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

