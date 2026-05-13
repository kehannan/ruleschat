"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""
import base64
import os
import json
import logging
import time
from pathlib import Path
from typing import Optional, Generator, Tuple, Any, Dict, List, Union

from app.asl.config import load_asl_config, ASLConfig
from app.asl.client import OpenAIResponsesClient
from app.asl.policy import build_instructions
from app.asl.postprocess import (
    extract_response_text,
    compute_timing_metrics
)
from app.asl.tools import TOOL_SCHEMAS, execute_tool

_IMAGE_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

_TERRAIN_LEGEND_PATH = Path(__file__).resolve().parents[2] / "static" / "img" / "terrain_legend.png"


def _load_terrain_legend_data_url() -> Optional[str]:
    if not _TERRAIN_LEGEND_PATH.is_file():
        logging.warning("Terrain legend not found at %s; multimodal calls will skip it", _TERRAIN_LEGEND_PATH)
        return None
    b64 = base64.b64encode(_TERRAIN_LEGEND_PATH.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


_TERRAIN_LEGEND_DATA_URL = _load_terrain_legend_data_url()

VISION_INSTRUCTIONS_ADDENDUM = """

Two images are attached. The FIRST is a fixed VASL terrain legend showing labeled examples of 12 terrain types: Open Ground, Road (Dirt), Road (Paved), Woods, Wooden Building, Stone Building, Wall, Hedge, Grain, Brush, Orchard, and Hill. The SECOND is the user's board screenshot.

Before naming any terrain on the user's board, do visual matching against the legend - compare each board hex's color, pattern, and shape to the legend cells, and pick the closest match. Do not rely on prior assumptions about VASL conventions; the legend is the source of truth for what each terrain looks like. Distinguish Wooden Building (+2 TEM) from Stone Building (+3 TEM) by color/texture - Wooden is reddish-brown, Stone is gray. Distinguish Road (Dirt) from Road (Paved) similarly.

Counters in VASL frequently appear ROTATED at angles (commonly 30-60 degrees) when a unit has moved, fired, or is in a special state - this is normal VASL behavior, not image corruption. Rotation flips the counter to show its "moved" / "fired" / "CX" face. Read counter labels (firepower-range-morale, gun caliber like 75LL, MA value, vehicle ID, leadership) regardless of orientation; mentally rotate the text. AFV counters carry small numeric details (Basic TH#, MA, Target Size) that are critical for to-hit calculations - extract them when readable, and explicitly say which fields are unreadable when they are not.

Then: describe what you see in the user's board (hexes visible, counters and their state - broken/CX/disrupted/pinned, apparent LOS lines, terrain identified via the legend). Call file_search for the rule sections that govern the situation depicted. Cite specific rule sections (e.g., A6.4) in your answer. Reason over both the image and the retrieved rules. If a counter, hex, or detail is unreadable, say so explicitly rather than guessing. Never make a rule claim without a file_search citation."""


def _build_multimodal_input(question: str, image_path: str) -> list:
    """Read image from disk, encode as data URL, return Responses API multipart input.

    Includes the fixed terrain legend as the first input_image (when available)
    so the model can do visual matching against canonical VASL terrain examples.
    """
    p = Path(image_path)
    if not p.is_absolute():
        p = Path("data/uploads") / image_path
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {p}")
    mime = _IMAGE_MIME_BY_EXT.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported image extension: {p.suffix}")
    b64 = base64.b64encode(p.read_bytes()).decode()
    data_url = f"data:{mime};base64,{b64}"

    content: List[Dict[str, Any]] = [{"type": "input_text", "text": question}]
    if _TERRAIN_LEGEND_DATA_URL is not None:
        content.append({"type": "input_image", "image_url": _TERRAIN_LEGEND_DATA_URL, "detail": "high"})
    content.append({"type": "input_image", "image_url": data_url, "detail": "high"})
    return [{"role": "user", "content": content}]


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
                    "vector_store_ids": self.config.all_vector_store_ids,
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
        use_agentic: bool = False,
        max_chunks: Optional[int] = None,
        image_path: Optional[str] = None
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
        if image_path:
            instructions = instructions + VISION_INSTRUCTIONS_ADDENDUM

        # Build input — multimodal if image attached, else plain string
        if image_path:
            api_input = _build_multimodal_input(question, image_path)
            logging.info(f"🖼️  Multimodal input built for image: {image_path}")
        else:
            api_input = question
        
        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            # Build tools - base tools
            num_chunks = max_chunks if max_chunks is not None else int(os.getenv("RAG_MAX_CHUNKS", "20"))
            tools = [
                {
                    "type": "file_search",
                    "vector_store_ids": self.config.all_vector_store_ids,
                    "max_num_results": num_chunks
                }
            ]
            
            # Add function tools if agentic mode is enabled
            if use_agentic:
                tools.extend(TOOL_SCHEMAS)
                logging.info(f"🤖 Agentic mode enabled - added {len(TOOL_SCHEMAS)} function tools")
            
            # Build common API kwargs — some models (e.g. gpt-5-mini) don't support temperature
            api_kwargs = {
                "model": model,
                "input": api_input,
                "instructions": instructions,
                "tools": tools,
            }
            # Only include temperature for models that support it
            _no_temp_models = {"gpt-5-mini", "gpt-5-mini-2025-08-07", "gpt-5.4-mini"}
            if model not in _no_temp_models:
                api_kwargs["temperature"] = temperature

            if stream:
                # Use stream_response for true streaming with final response access
                stream_manager = self.client.stream_response(**api_kwargs)
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
                    api_kwargs["stream"] = False
                    response = self.client.create_response(**api_kwargs)
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

                        # Extract token usage
                        if hasattr(final, 'usage') and final.usage:
                            timing_data["input_tokens"] = getattr(final.usage, 'input_tokens', 0)
                            timing_data["output_tokens"] = getattr(final.usage, 'output_tokens', 0)
                            logging.info(f"📊 Tokens: {timing_data['input_tokens']} in / {timing_data['output_tokens']} out")
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

        # Track previous response ID for context
        previous_response_id = None
        input_data = question

        for iteration in range(max_iterations):
            logging.info(f"🔄 Agentic iteration {iteration + 1}/{max_iterations}")

            # Make API call (use previous_response_id if available)
            if previous_response_id:
                response = self.client.create_response(
                    model=model,
                    input=input_data,
                    previous_response_id=previous_response_id,
                    instructions=instructions,
                    temperature=temperature,
                    stream=False,
                    tools=tools
                )
            else:
                response = self.client.create_response(
                    model=model,
                    input=input_data,
                    instructions=instructions,
                    temperature=temperature,
                    stream=False,
                    tools=tools
                )
            
            # Store response ID for next iteration
            previous_response_id = getattr(response, "id", None)

            # Extract output blocks
            output_blocks = getattr(response, "output", [])

            # Find function calls and extract final text
            function_calls = []
            final_text = None

            for block in output_blocks:
                b_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)

                if b_type == "function_call":
                    function_calls.append({
                        "call_id": block.get("call_id") if isinstance(block, dict) else getattr(block, "call_id", None),
                        "name": block.get("name") if isinstance(block, dict) else getattr(block, "name", None),
                        "arguments": block.get("arguments") if isinstance(block, dict) else getattr(block, "arguments", "{}")
                    })
                elif b_type == "message":
                    # Extract text output
                    content = block.get("content") if isinstance(block, dict) else getattr(block, "content", [])
                    for item in content:
                        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
                        if item_type == "output_text":
                            final_text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            
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
            
            # 2. Execute function calls and build input for next iteration
            logging.info(f"🔧 Executing {len(function_calls)} function call(s)...")

            # Build array of function results
            function_results = []
            for fc in function_calls:
                call_id = fc.get("call_id")
                name = fc.get("name")
                args_raw = fc.get("arguments")

                try:
                    args = json_module.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    logging.info(f"  📞 {name}({args})")
                    result = execute_tool(name, args)
                    result_json = json_module.dumps(result)

                    function_results.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result_json
                    })
                    logging.info(f"  ✅ Result: {result_json[:100]}...")
                except Exception as e:
                    logging.error(f"  ❌ Tool error: {e}")
                    function_results.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json_module.dumps({"error": str(e)})
                    })

            # Set input_data to function results for next iteration
            input_data = function_results
        
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
