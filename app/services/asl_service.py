"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""
import os
import json
import logging
import time
from typing import Optional, Generator, Tuple, Any, Dict, List

from app.asl.config import load_asl_config, ASLConfig
from app.asl.client import OpenAIResponsesClient
from app.asl.policy import build_instructions
from app.asl.postprocess import (
    extract_response_text,
    compute_timing_metrics
)
from app.asl.tools import TOOL_SCHEMAS, execute_tool


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
    
    def get_answer(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        return_timing: bool = False,
        force_web_search: bool = False,
        use_verification: bool = False,
        use_agentic: bool = False
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
            use_agentic: If True, enables function tools for calculations (non-streaming only)
            
        Returns:
            The answer as a string (or generator if stream=True)
            If return_timing=True and stream=True, returns (generator, timing_data)
            
        Note:
            use_verification and use_agentic require stream=False
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        # Validation for special modes
        if use_verification and stream:
            raise ValueError("Verification is only supported in non-streaming mode (stream=False)")
        if use_agentic and stream:
            raise ValueError("Agentic mode is only supported in non-streaming mode (stream=False)")
        
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        
        # Build instructions
        instructions = build_instructions(
            self.config.system_instructions,
            question,
            force_web_search=force_web_search
        )
        
        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            # Build tools - base tools
            tools = [
                {
                    "type": "file_search",
                    "vector_store_ids": [self.config.vector_store_id],
                    "max_num_results": 5
                },
                {
                    "type": "web_search",
                }
            ]
            
            # Add function tools if agentic mode is enabled
            if use_agentic:
                tools.extend(TOOL_SCHEMAS)
                logging.info(f"🤖 Agentic mode enabled - added {len(TOOL_SCHEMAS)} function tools")
            
            if stream:
                # Use stream_response for true streaming with final response access
                stream_manager = self.client.stream_response(
                    model=model,
                    input=question,
                    instructions=instructions,
                    temperature=temperature,
                    tools=tools
                )
                return self._handle_streaming_response(
                    stream_manager,
                    api_call_start_time,
                    return_timing
                )
            else:
                # Non-streaming mode
                if use_agentic:
                    # Use agentic handler with tool execution loop
                    return self._handle_agentic_response(
                        question=question,
                        instructions=instructions,
                        model=model,
                        temperature=temperature,
                        tools=tools,
                        api_call_start_time=api_call_start_time,
                        use_verification=use_verification
                    )
                else:
                    # Standard non-streaming
                    response = self.client.create_response(
                        model=model,
                        input=question,
                        instructions=instructions,
                        temperature=temperature,
                        stream=False,
                        tools=tools
                    )
                    return self._handle_non_streaming_response(
                        response,
                        api_call_start_time,
                        question,
                        model,
                        temperature,
                        use_verification
                    )
                
        except Exception as e:
            error_msg = f"Error getting response: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _handle_streaming_response(
        self,
        stream_manager,
        api_call_start_time: float,
        return_timing: bool
    ) -> Tuple[Generator[str, None, None], Optional[Dict[str, Any]]]:
        """
        Handle streaming response (true streaming + final response capture).
        
        Returns:
            Tuple of (stream_generator, timing_data) if return_timing=True
            Otherwise (stream_generator, empty list)
        """
        def _extract_rag_sources_from_final(final_response) -> list:
            """Extract vector store results from final response.output."""
            output = getattr(final_response, "output", None)
            if not output:
                return []

            def _get(obj, key, default=None):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)

            rag_results: list = []
            for item in output:
                if _get(item, "type") != "file_search_call":
                    continue
                results = _get(item, "results", []) or []
                for r in results:
                    # openai-python Result fields:
                    # - text: retrieved chunk text
                    # - filename: original uploaded filename (if available)
                    # - attributes: metadata dict (if any)
                    attributes = _get(r, "attributes", None)
                    filename = _get(r, "filename", None)
                    text = _get(r, "text", None)
                    # Backwards/alternate field fallbacks
                    if text is None:
                        text = _get(r, "content", None)

                    rag_results.append(
                        {
                            "index": len(rag_results) + 1,
                            "file_id": _get(r, "file_id"),
                            "score": _get(r, "score"),
                            "content": text or "",
                            "attributes": attributes if isinstance(attributes, dict) else (attributes.__dict__ if hasattr(attributes, "__dict__") else None),
                            "filename": filename or "Unknown",
                        }
                    )
            return rag_results

        # Use a mutable dict to capture timing data from the generator closure
        timing_data: Dict[str, Any] = {} if return_timing else {}

        def stream_generator():
            first_event_time = None
            file_search_complete_time = None
            first_delta_time = None
                
            with stream_manager as stream:
                for event in stream:
                    if first_event_time is None:
                        first_event_time = time.time()
                        first_event_ms = (first_event_time - api_call_start_time) * 1000
                        logging.info(
                            f"[RAG Latency] First event received: {first_event_ms:.1f}ms (type: {getattr(event, 'type', 'unknown')})"
                        )

                    if file_search_complete_time is None and hasattr(event, 'type') and event.type == 'response.file_search_call.completed':
                        file_search_complete_time = time.time()
                        file_search_time_ms = (file_search_complete_time - api_call_start_time) * 1000
                        logging.info(f"[RAG Latency] File search completed: {file_search_time_ms:.1f}ms")
                    
                    if first_delta_time is None and hasattr(event, 'type') and event.type == 'response.output_text.delta':
                        first_delta_time = time.time()
                        ttft_ms = (first_delta_time - api_call_start_time) * 1000
                        logging.info(f"[RAG Latency] First token (TTFT): {ttft_ms:.1f}ms")
                    
                    # Yield deltas immediately for true streaming
                    if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                        delta = getattr(event, 'delta', None)
                        if delta:
                            yield delta
                
                # After stream completes, extract RAG sources from final response
                stream_end_time = time.time()
                if return_timing:
                    timing_data.update(compute_timing_metrics(
                        api_call_start_time,
                        first_event_time,
                        file_search_complete_time,
                        first_delta_time,
                        stream_end_time
                    ))

                    try:
                        final = stream.get_final_response()
                        rag_sources = _extract_rag_sources_from_final(final)
                        timing_data["rag_sources"] = rag_sources
                        logging.info(f"📚 Extracted {len(rag_sources)} RAG sources from final response")
                    except Exception as e:
                        logging.warning(f"⚠️ Failed to extract RAG sources from final response: {e}", exc_info=True)
                        timing_data["rag_sources"] = []

        generator = stream_generator()
        if return_timing:
            # Note: timing_data will be populated after generator is fully consumed
            # The caller must consume the generator first, then timing_data will be available
            return generator, timing_data
        return generator, []
    
    def _handle_non_streaming_response(
        self,
        response,
        api_call_start_time: float,
        question: str,
        model: Optional[str],
        temperature: Optional[float],
        use_verification: bool
    ) -> str:
        """Handle non-streaming response."""
        response_start_time = time.time()
        response_text = extract_response_text(response)
        response_end_time = time.time()
                
        total_time_ms = (response_end_time - api_call_start_time) * 1000
        logging.info(f"[RAG Latency] Total response time (non-streaming): {total_time_ms:.1f}ms")
                
        # Apply verification if enabled
        if use_verification:
            logging.info("🔍 Verification enabled - running second pass...")
            response_text = self._verify_answer(question, response_text, model, temperature)
        
        return response_text
    
    def _handle_agentic_response(
        self,
        question: str,
        instructions: str,
        model: str,
        temperature: float,
        tools: List[Dict[str, Any]],
        api_call_start_time: float,
        use_verification: bool,
        max_iterations: int = 5
    ) -> str:
        """
        Handle agentic response with multi-turn tool execution loop.
        """
        import json as json_module
        
        logging.info("🤖 Starting agentic response loop...")
        
        # Conversation history for multi-turn
        messages = [{"role": "user", "content": question}]
        
        for iteration in range(max_iterations):
            logging.info(f"🔄 Agentic iteration {iteration + 1}/{max_iterations}")
            
            # Make API call
            response = self.client.create_response(
                model=model,
                input=messages,
                instructions=instructions,
                temperature=temperature,
                stream=False,
                tools=tools
            )
            
            # Extract output blocks
            output_blocks = getattr(response, "output", [])
            
            # 1. Add THIS response to history (crucial for tool call context)
            # IMPORTANT: Sanitize output_blocks to only include 'function_call' and 'message' (with 'output_text')
            # The API rejects 'file_search_call' blocks when sent back in 'input'.
            sanitized_outputs = []
            for b in output_blocks:
                b_type = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                
                if b_type == "function_call":
                    # Function call blocks are required
                    sanitized_outputs.append({
                        "type": "function_call",
                        "call_id": b.get("call_id") if isinstance(b, dict) else getattr(b, "call_id", None),
                        "name": b.get("name") if isinstance(b, dict) else getattr(b, "name", None),
                        "arguments": b.get("arguments") if isinstance(b, dict) else getattr(b, "arguments", "{}")
                    })
                elif b_type == "message":
                    # Content messages are allowed. Convert sub-items (like ResponseOutputText) to dicts.
                    content_raw = b.get("content") if isinstance(b, dict) else getattr(b, "content", [])
                    sanitized_content = []
                    for c in content_raw:
                        if hasattr(c, "to_dict"):
                            sanitized_content.append(c.to_dict())
                        elif isinstance(c, dict):
                            sanitized_content.append(c)
                        else:
                            # Try getattr for common fields if not a dict and no to_dict
                            sanitized_content.append({
                                "type": getattr(c, "type", "unknown"),
                                "text": getattr(c, "text", "") if hasattr(c, "text") else ""
                            })
                    
                    sanitized_outputs.append({
                        "type": "message",
                        "content": sanitized_content
                    })
            
            messages.append({
                "role": "assistant",
                "content": sanitized_outputs
            })
            
            # Find function calls and text output
            function_calls = [b for b in sanitized_outputs if b["type"] == "function_call"]
            final_text = None
            
            # Extract text output if present in the message blocks
            for block in sanitized_outputs:
                if block["type"] == "message":
                    content = block.get("content", [])
                    for sub in content:
                        sub_type = sub.get("type") if isinstance(sub, dict) else getattr(sub, "type", None)
                        if sub_type == "output_text":
                            final_text = sub.get("text") if isinstance(sub, dict) else getattr(sub, "text", None)
            
            # If no function calls, we have our final answer
            if not function_calls:
                logging.info(f"✅ Agentic loop completed after {iteration + 1} iterations")
                if final_text is None:
                    final_text = extract_response_text(response)
                
                response_end_time = time.time()
                total_time_ms = (response_end_time - api_call_start_time) * 1000
                logging.info(f"[RAG Latency] Total agentic response time: {total_time_ms:.1f}ms")
                
                if use_verification:
                    logging.info("🔍 Verification enabled - running second pass...")
                    final_text = self._verify_answer(question, final_text, model, temperature)
                
                return final_text
            
            # 2. Execute function calls and add their results to history
            logging.info(f"🔧 Executing {len(function_calls)} function call(s)...")
            
            for fc in function_calls:
                call_id = fc.get("call_id")
                name = fc.get("name")
                args_raw = fc.get("arguments")
                
                try:
                    args = json_module.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    logging.info(f"  📞 {name}({args})")
                    result = execute_tool(name, args)
                    result_json = json_module.dumps(result)
                    
                    messages.append({
                        "role": "tool",
                        "call_id": call_id,
                        "content": result_json
                    })
                    logging.info(f"  ✅ Result: {result_json[:100]}...")
                except Exception as e:
                    logging.error(f"  ❌ Tool error: {e}")
                    messages.append({
                        "role": "tool",
                        "call_id": call_id,
                        "content": json_module.dumps({"error": str(e)})
                    })
        
        logging.warning("⚠️ Max iterations reached")
        return final_text or extract_response_text(response)


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
