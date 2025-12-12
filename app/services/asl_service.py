"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""
import os
import json
import logging
import time
from typing import Optional, Generator, Tuple, Any, Dict

from app.asl.config import load_asl_config, ASLConfig
from app.asl.client import OpenAIResponsesClient
from app.asl.policy import build_instructions, is_calculation_question
from app.asl.postprocess import (
    extract_response_text,
    format_structured_answer,
    parse_structured_json,
    compute_timing_metrics
)


class ASLService:
    """Service for getting ASL rule answers via Responses API."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        vector_store_id: Optional[str] = None,
        config_file: Optional[str] = None
    ):
        """
        Initialize ASL Service.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            vector_store_id: Vector store ID (defaults to loading from config file)
            config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        """
        self.config = load_asl_config(api_key, vector_store_id, config_file)
        self.client = OpenAIResponsesClient(self.config)
        
        logging.info(f"ASL Service initialized with vector store: {self.config.vector_store_id}")
    
    def _verify_answer(
        self, 
        question: str, 
        initial_answer: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Verify and potentially correct an initial answer.
        
        Args:
            question: The original question
            initial_answer: The initial answer to verify
            model: Model to use for verification
            temperature: Temperature for verification (use 0 for deterministic)
            
        Returns:
            Verified/corrected answer
        """
        verification_prompt = f"""VERIFICATION TASK

Original Question: {question}

Initial Answer: {initial_answer}

Your task is to verify this answer for completeness and correctness. Check:

1. COMPLETENESS - Did the answer consider ALL applicable modifiers?
   - For blind hexes: elevation advantage, range, obstacle height
   - For DRMs: terrain, leadership, range, unit status, special cases
   - For Residual FP: division by 2, column shifts for hindrances
   - For any calculation: all relevant rules and exceptions

2. CALCULATION ACCURACY - Were all math steps shown and correct?
   - Check each arithmetic operation
   - Verify column shifts were applied correctly
   - Confirm division/multiplication steps

3. RULE APPLICATION - Were rules cited and applied correctly?
   - Check section references are accurate
   - Verify rule interpretation matches the question
   - Look for missing or misapplied rules

4. PERSPECTIVE/DIRECTION - Was the question answered from the correct viewpoint?
   - Attacking FROM vs being attacked IN
   - Firer vs target
   - Moving unit vs stationary unit

If you find ANY errors or omissions, provide the CORRECTED answer with:
- Explanation of what was wrong/missing
- Complete corrected calculation
- Final corrected answer

If the answer is correct and complete, respond with:
"VERIFIED: The initial answer is correct and complete."

Your response:"""

        logging.info("🔍 Running verification pass...")
        
        try:
            response = self.client.create_response(
                model=model or self.config.model,
                input=verification_prompt,
                instructions=self.config.system_instructions,
                temperature=0.0,  # Use 0 for deterministic verification
                stream=False,
                tools=[{
                        "type": "file_search",
                    "vector_store_ids": [self.config.vector_store_id],
                }]
            )
            
            verified_answer = extract_response_text(response)
            
            # Check if verification found issues
            if "VERIFIED:" in verified_answer and "correct and complete" in verified_answer.lower():
                logging.info("✅ Verification passed - initial answer is correct")
                return initial_answer
            else:
                logging.info("⚠️ Verification found issues - using corrected answer")
                return verified_answer
                
        except Exception as e:
            logging.error(f"❌ Verification failed: {e}")
            # Fall back to initial answer if verification fails
            return initial_answer
    
    def _extract_citations_simple(self, events: list) -> list:
        """
        Extract citations from events using only official/available fields.
        Gated behind DEBUG_RAG=1 for verbose logging.
        
        Args:
            events: List of streaming events
            
        Returns:
            List of citation dicts with metadata
        """
        debug_rag = os.getenv("DEBUG_RAG", "0") == "1"
        citations = []
        
        if debug_rag:
            logging.info(f"🔍 [DEBUG_RAG] Starting citation extraction from {len(events)} events...")
        
        for event in events:
            event_type = getattr(event, 'type', 'unknown')
            
            if event_type == 'response.output_text.annotation.added':
                if debug_rag:
                    logging.info(f"   🔎 [DEBUG_RAG] Found annotation.added event!")
                
                try:
                    if hasattr(event, 'annotation'):
                        annotation = event.annotation
                        
                        # Extract file citations from annotation
                        if isinstance(annotation, dict):
                            if annotation.get('type') == 'file_citation':
                                file_id = annotation.get('file_id')
                                filename = annotation.get('filename', '')
                                citation_index = annotation.get('index')
                                
                                if debug_rag:
                                    logging.info(f"      [DEBUG_RAG] Found file_citation: file_id={file_id}, index={citation_index}, filename={filename}")
                                
                                citation_id = f"{file_id}:{citation_index}"
                                
                                # Check if we already have this citation
                                citation_exists = any(c.get('id') == citation_id for c in citations)
                                if not citation_exists:
                                    new_citation = {
                                        'id': citation_id,
                                        'index': len(citations) + 1,
                                        'file_id': file_id,
                                        'filename': filename,
                                        'chunk_index': citation_index,
                                        'content': ''  # Content not available in streaming events
                                    }
                                    citations.append(new_citation)
                                    if debug_rag:
                                        logging.info(f"      [DEBUG_RAG] ✅ Added citation {new_citation['index']}: {citation_id}")
                        elif hasattr(annotation, 'file_citations'):
                            # Object access path
                            file_citations = annotation.file_citations
                            for file_citation in file_citations:
                                if hasattr(file_citation, 'quote'):
                                    citation_text = file_citation.quote
                                    if citation_text and citation_text.strip():
                                        citation_exists = any(c.get('content') == citation_text for c in citations)
                                        if not citation_exists:
                                            citations.append({
                                                'index': len(citations) + 1,
                                                'content': citation_text.strip()
                                            })
                except Exception as e:
                    if debug_rag:
                        logging.warning(f"      [DEBUG_RAG] Error extracting citations from annotation: {e}", exc_info=True)
        
        if debug_rag:
            logging.info(f"📎 [DEBUG_RAG] Extracted {len(citations)} citations")
        
        return citations
    
    def get_answer(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        return_timing: bool = False,
        force_web_search: bool = False,
        use_verification: bool = False,
        use_structured_output: bool = False
    ):
        """
        Get an answer to an ASL question.
        
        Args:
            question: The ASL question to ask
            stream: Whether to stream the response (returns generator if True)
            model: Override default model
            temperature: Override default temperature
            return_timing: If True and stream=True, returns tuple (generator, timing_data)
            force_web_search: If True, emphasizes web search usage in instructions
            use_verification: If True, uses two-pass verification to check answer
            use_structured_output: If True, forces JSON structured output for calculations
            
        Returns:
            The answer as a string (or generator if stream=True)
            If return_timing=True and stream=True, returns (generator, timing_data)
            
        Note:
            use_verification and use_structured_output require stream=False
            use_verification and use_structured_output are mutually exclusive
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        # Validation for special modes
        if use_verification and stream:
            raise ValueError("Verification is only supported in non-streaming mode (stream=False)")
        
        if use_structured_output and stream:
            raise ValueError("Structured output is only supported in non-streaming mode (stream=False)")
        
        if use_verification and use_structured_output:
            raise ValueError("Cannot use both verification and structured output simultaneously")
        
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        
        # Build instructions
        instructions = build_instructions(
            self.config.system_instructions,
            question,
            force_web_search=force_web_search,
            use_structured_output=use_structured_output
        )
        
        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            # Build tools
            tools = [
                {
                    "type": "file_search",
                    "vector_store_ids": [self.config.vector_store_id],
                },
                {
                    "type": "web_search",
                }
            ]
            
            # Use Responses API with file_search and web_search tools
            response = self.client.create_response(
                model=model,
                input=question,
                instructions=instructions,
                temperature=temperature,
                stream=stream,
                tools=tools
            )
            
            if stream:
                return self._handle_streaming_response(
                    response,
                    api_call_start_time,
                    return_timing,
                    use_structured_output
                )
            else:
                return self._handle_non_streaming_response(
                    response,
                    api_call_start_time,
                    question,
                    model,
                    temperature,
                    use_verification,
                    use_structured_output
                )
                
        except Exception as e:
            error_msg = f"Error getting response: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _handle_streaming_response(
        self,
        response,
        api_call_start_time: float,
        return_timing: bool,
        use_structured_output: bool
    ) -> Tuple[Generator[str, None, None], Optional[Dict[str, Any]]]:
        """
        Handle streaming response.
        
        Returns:
            Tuple of (stream_generator, timing_data) if return_timing=True
            Otherwise just stream_generator
        """
        events = []
        output_text = ""
        
        # Timing variables
        first_event_time = None
        file_search_complete_time = None
        first_delta_time = None
        stream_end_time = None
        
        for event in response:
            # Track first event
            if first_event_time is None:
                first_event_time = time.time()
                first_event_time_ms = (first_event_time - api_call_start_time) * 1000
                event_type = getattr(event, 'type', 'unknown')
                logging.info(f"[RAG Latency] First event received: {first_event_time_ms:.1f}ms (type: {event_type})")
            
            # Track file_search completion
            if file_search_complete_time is None and hasattr(event, 'type') and event.type == 'response.file_search_call.completed':
                file_search_complete_time = time.time()
                file_search_time_ms = (file_search_complete_time - api_call_start_time) * 1000
                logging.info(f"[RAG Latency] File search completed: {file_search_time_ms:.1f}ms")
            
            # Track first token (TTFT - Time To First Token)
            if first_delta_time is None and hasattr(event, 'type') and event.type == 'response.output_text.delta':
                first_delta_time = time.time()
                ttft_ms = (first_delta_time - api_call_start_time) * 1000
                logging.info(f"[RAG Latency] First token (TTFT): {ttft_ms:.1f}ms")
            
            events.append(event)
            if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                if hasattr(event, 'delta') and event.delta:
                    output_text += event.delta
        
        # Track stream end
        stream_end_time = time.time()
        total_streaming_time_ms = (stream_end_time - api_call_start_time) * 1000
        
        # Calculate timing metrics
        timing_data = compute_timing_metrics(
            api_call_start_time,
            first_event_time,
            file_search_complete_time,
            first_delta_time,
            stream_end_time
        )
        
        # Log latency summary
        logging.info(f"[RAG Latency] Total streaming time: {total_streaming_time_ms:.1f}ms")
        if file_search_complete_time:
            generation_time_ms = timing_data.get("generation_time_ms")
            logging.info(f"[RAG Latency] Generation time (after RAG): {generation_time_ms:.1f}ms")
        if first_delta_time:
            rag_to_first_token_ms = (first_delta_time - api_call_start_time) * 1000
            logging.info(f"[RAG Latency] RAG + initial generation: {rag_to_first_token_ms:.1f}ms")
                
        # Extract citations (simplified, gated behind DEBUG_RAG)
        citations = self._extract_citations_simple(events) if return_timing else []
        
        # Store citations in timing_data if return_timing is True
        if return_timing:
            timing_data['citations'] = citations
            return self._stream_generator(events), timing_data
        
        # If not returning timing, return stream and empty citations
        return self._stream_generator(events), []
    
    def _handle_non_streaming_response(
        self,
        response,
        api_call_start_time: float,
        question: str,
        model: Optional[str],
        temperature: Optional[float],
        use_verification: bool,
        use_structured_output: bool
        ) -> str:
        """Handle non-streaming response."""
        response_start_time = time.time()
        response_text = extract_response_text(response)
        response_end_time = time.time()
        
        total_time_ms = (response_end_time - api_call_start_time) * 1000
        logging.info(f"[RAG Latency] Total response time (non-streaming): {total_time_ms:.1f}ms")
        
        # Apply structured output parsing if enabled
        if use_structured_output:
            logging.info("📋 Structured output enabled - parsing JSON response...")
            try:
                structured_data = parse_structured_json(response_text)
                response_text = format_structured_answer(structured_data)
                logging.info("✅ Successfully parsed and formatted structured output")
            except json.JSONDecodeError as e:
                logging.error(f"❌ Failed to parse JSON response: {e}")
                logging.error(f"Raw response: {response_text[:500]}")
                # Fall back to raw response if JSON parsing fails
                response_text = f"Error parsing structured output:\n\n{response_text}"
        
        # Apply verification if enabled
        if use_verification:
            logging.info("🔍 Verification enabled - running second pass...")
            response_text = self._verify_answer(question, response_text, model, temperature)
        
        # Return complete response
        return response_text
    
    def _stream_generator(self, events: list) -> Generator[str, None, None]:
        """Generate text deltas from events."""
        for event in events:
            if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                if hasattr(event, 'delta') and event.delta:
                    yield event.delta


# Global service instance (lazy initialization)
_global_service: Optional[ASLService] = None


def get_asl_service(
    api_key: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    config_file: Optional[str] = None
) -> ASLService:
    """
    Get the global ASL service instance (singleton pattern).
    
    Args:
        api_key: Optional API key (only used if creating new instance)
        vector_store_id: Optional vector store ID (only used if creating new instance)
        config_file: Optional config file path (only used if creating new instance)
        
    Returns:
        ASLService instance
    """
    global _global_service
    
    if _global_service is None:
        _global_service = ASLService(
            api_key=api_key,
            vector_store_id=vector_store_id,
            config_file=config_file
        )
    
    return _global_service


def reset_service():
    """Reset the global service instance (useful for testing)."""
    global _global_service
    _global_service = None
