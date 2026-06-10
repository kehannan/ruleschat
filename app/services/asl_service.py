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

from openai import OpenAI

from app.asl.config import load_asl_config, ASLConfig
from app.asl.client import OpenAIResponsesClient
from app.asl.openrouter_client import build_openrouter_client_from_env
from app.asl.retrieval import retrieve_chunks, format_chunks_as_context
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

Multiple images are attached. The FIRST image is a fixed VASL terrain legend showing labeled examples of 12 terrain types: Open Ground, Road (Dirt), Road (Paved), Woods, Wooden Building, Stone Building, Wall, Hedge, Grain, Brush, Orchard, and Hill. The REMAINING image(s) are the user's board screenshot(s) - the user may attach more than one view of the same situation (for example: a wide view plus a zoomed-in detail of specific counters). Treat all user images as views of the same game situation unless they obviously depict different scenes.

Before naming any terrain on the user's board, do visual matching against the legend - compare each board hex's color, pattern, and shape to the legend cells, and pick the closest match. Do not rely on prior assumptions about VASL conventions; the legend is the source of truth for what each terrain looks like. Distinguish Wooden Building (+2 TEM) from Stone Building (+3 TEM) by color/texture - Wooden is reddish-brown, Stone is gray. Distinguish Road (Dirt) from Road (Paved) similarly.

Counters in VASL frequently appear ROTATED at angles (commonly 30-60 degrees) when a unit has moved, fired, or is in a special state - this is normal VASL behavior, not image corruption. Rotation flips the counter to show its "moved" / "fired" / "CX" face. Read counter labels (firepower-range-morale, gun caliber like 75LL, MA value, vehicle ID, leadership) regardless of orientation; mentally rotate the text. AFV counters carry small numeric details (Basic TH#, MA, Target Size) that are critical for to-hit calculations - extract them when readable, and explicitly say which fields are unreadable when they are not. When multiple user views are attached, prefer the highest-detail view for reading small counter labels.

Then: describe what you see across the user's board view(s) (hexes visible, counters and their state - broken/CX/disrupted/pinned, apparent LOS lines, terrain identified via the legend). Call file_search for the rule sections that govern the situation depicted. Cite specific rule sections (e.g., A6.4) in your answer. Reason over both the image(s) and the retrieved rules. If a counter, hex, or detail is unreadable, say so explicitly rather than guessing. Never make a rule claim without a file_search citation."""


def _read_image_as_data_url(image_path: str) -> str:
    """Decode a stored image file into a base64 data URL for the Responses API."""
    p = Path(image_path)
    if not p.is_absolute():
        p = Path("data/uploads") / image_path
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {p}")
    mime = _IMAGE_MIME_BY_EXT.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported image extension: {p.suffix}")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _build_multimodal_input(question: str, image_paths: List[str]) -> list:
    """Build the Responses API multipart input for one or more attached images.

    Order of input_image blocks: terrain legend first (if available, as a
    fixed visual reference), then each user image in the order pasted.
    """
    if not image_paths:
        raise ValueError("_build_multimodal_input requires at least one image_path")

    content: List[Dict[str, Any]] = [{"type": "input_text", "text": question}]
    if _TERRAIN_LEGEND_DATA_URL is not None:
        content.append({"type": "input_image", "image_url": _TERRAIN_LEGEND_DATA_URL, "detail": "high"})
    for path in image_paths:
        data_url = _read_image_as_data_url(path)
        content.append({"type": "input_image", "image_url": data_url, "detail": "high"})
    return [{"role": "user", "content": content}]


def _get(obj, key, default=None):
    """Read a field from a dict or an SDK object uniformly."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _output_function_calls(output) -> List[Dict[str, Any]]:
    """Pull function_call blocks out of a Responses API `output` list."""
    calls: List[Dict[str, Any]] = []
    for block in output or []:
        if _get(block, "type") == "function_call":
            calls.append({
                "call_id": _get(block, "call_id"),
                "name": _get(block, "name"),
                "arguments": _get(block, "arguments", "{}"),
            })
    return calls


def _extract_rag_sources_from_output(output) -> List[Dict[str, Any]]:
    """Extract file_search vector-store results from a Responses API `output` list."""
    rag_results: List[Dict[str, Any]] = []
    for item in output or []:
        if _get(item, "type") != "file_search_call":
            continue
        for r in _get(item, "results", []) or []:
            attributes = _get(r, "attributes", None)
            text = _get(r, "text", None) or _get(r, "content", None)
            rag_results.append({
                "index": len(rag_results) + 1,
                "file_id": _get(r, "file_id"),
                "score": _get(r, "score"),
                "content": text or "",
                "attributes": attributes if isinstance(attributes, dict) else (
                    attributes.__dict__ if hasattr(attributes, "__dict__") else None
                ),
                "filename": _get(r, "filename", None) or "Unknown",
            })
    return rag_results


_PUA_LO, _PUA_HI = 0xE000, 0xF8FF  # Unicode Basic Multilingual Plane Private Use Area


def _is_pua(ch: str) -> bool:
    return _PUA_LO <= ord(ch) <= _PUA_HI


class _CitationStripper:
    """Remove OpenAI file_search citation markers from a streamed token sequence.

    The Responses API emits inline citations like
    ``fileciteturn0file3`` — Private Use Area delimiter
    characters wrapping an alphanumeric citation token, with no embedded
    whitespace. Normal rulebook text never contains PUA characters, so a PUA
    char reliably marks the start of one. This is stateful so a marker split
    across streaming chunks is still removed; whitespace immediately preceding a
    marker is dropped so the text doesn't end up with double spaces.
    """

    def __init__(self) -> None:
        self._in_marker = False
        self._pending_ws = ""

    def feed(self, text: str) -> str:
        out: List[str] = []
        for ch in text:
            if self._in_marker:
                if _is_pua(ch) or ch.isalnum():
                    continue  # still inside the marker
                self._in_marker = False  # terminator — handle it below
            if _is_pua(ch):
                self._in_marker = True
                self._pending_ws = ""  # drop whitespace that preceded the marker
            elif ch.isspace():
                self._pending_ws += ch
            else:
                if self._pending_ws:
                    out.append(self._pending_ws)
                    self._pending_ws = ""
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        ws, self._pending_ws = self._pending_ws, ""
        return ws

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
        # Plain OpenAI client for client-side vector-store search (used when
        # the LLM call goes through OpenRouter and we can't piggyback on the
        # Responses API's server-side file_search).
        self.retrieval_client = OpenAI(api_key=self.config.api_key)
        # OpenRouter client — None if OPENROUTER_API_KEY isn't set.
        # Selecting a "/" model when this is None raises a clear error.
        self.openrouter_client = build_openrouter_client_from_env()

        logging.info(f"ASL Service initialized with vector store: {self.config.vector_store_id}")
        if self.openrouter_client:
            logging.info("OpenRouter client initialized (/-prefixed model names route here)")
    
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
        image_paths: Optional[List[str]] = None,
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
            use_agentic: If True, exposes the IFT / TH-TK function tools. Works in
                both streaming and non-streaming modes (streaming resolves tool
                calls, then streams the final answer).

        Returns:
            The answer as a string (or generator if stream=True)
            If return_timing=True and stream=True, returns (generator, timing_data)

        Note:
            use_verification requires stream=False.
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        # Validation for special modes
        if use_verification and stream:
            raise ValueError("Verification is only supported in non-streaming mode (stream=False)")
        
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        
        # Build instructions
        instructions = build_instructions(
            self.config.system_instructions,
            question,
            force_web_search=force_web_search
        )
        if image_paths:
            instructions = instructions + VISION_INSTRUCTIONS_ADDENDUM

        # Build input — multimodal if image(s) attached, else plain string
        if image_paths:
            api_input = _build_multimodal_input(question, image_paths)
            logging.info(f"🖼️  Multimodal input built for {len(image_paths)} image(s): {image_paths}")
        else:
            api_input = question
        
        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            num_chunks = max_chunks if max_chunks is not None else int(os.getenv("RAG_MAX_CHUNKS", "20"))

            # OpenRouter path: model names like "deepseek/deepseek-v3.2" go here.
            # We do retrieval client-side via the OpenAI vector store, bake the
            # chunks into the system prompt, and call OpenRouter for inference.
            # Always non-streaming for now — the chat WebSocket gets the full
            # answer as one delta. Image inputs aren't supported on this path.
            if "/" in model:
                if self.openrouter_client is None:
                    raise RuntimeError(
                        f"Model '{model}' requires OpenRouter, but OPENROUTER_API_KEY "
                        "is not set on this deployment."
                    )
                if image_paths:
                    raise ValueError(
                        f"Model '{model}' (OpenRouter) does not support image inputs."
                    )
                if use_agentic or use_verification:
                    raise ValueError(
                        "use_agentic / use_verification are not supported on the OpenRouter path."
                    )
                return self._openrouter_answer(
                    question=question,
                    model=model,
                    temperature=temperature,
                    instructions=instructions,
                    num_chunks=num_chunks,
                    api_call_start_time=api_call_start_time,
                    stream=stream,
                    return_timing=return_timing,
                )

            # Build tools - base tools
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
                if use_agentic:
                    # Resolve tool calls, then stream the final answer. Same
                    # (generator, timing_data) contract as the plain stream path.
                    return self._handle_agentic_streaming_response(
                        input_data=api_input,
                        instructions=instructions,
                        model=model,
                        temperature=api_kwargs.get("temperature"),
                        tools=tools,
                        api_call_start_time=api_call_start_time,
                        return_timing=return_timing,
                    )
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

    def _openrouter_answer(
        self,
        question: str,
        model: str,
        temperature: float,
        instructions: str,
        num_chunks: int,
        api_call_start_time: float,
        stream: bool,
        return_timing: bool,
    ):
        """
        OpenRouter path: client-side retrieval + non-streaming inference.

        Returns the same shape the OpenAI path returns:
          * stream=True  → (generator, timing_data). The generator yields the
                           whole answer as ONE delta (we're non-streaming under
                           the hood; the chat WebSocket sees a single chunk
                           after the call completes).
          * stream=False → answer string.

        timing_data is populated *before* the generator is consumed (the
        whole call is synchronous), unlike the OpenAI streaming path where
        timing_data fills in during iteration.
        """
        # 1. Retrieval — OpenAI vector store search.
        retrieval_start = time.time()
        chunks = retrieve_chunks(
            self.retrieval_client,
            self.config.all_vector_store_ids,
            query=question,
            max_results_per_store=num_chunks,
        )
        context_block = format_chunks_as_context(chunks)
        retrieval_ms = (time.time() - retrieval_start) * 1000
        logging.info(f"[RAG Latency] OpenRouter retrieval: {retrieval_ms:.1f}ms ({len(chunks)} chunks)")

        # 2. Build messages with retrieved context baked into the system prompt.
        sys_with_context = instructions
        if context_block:
            sys_with_context = (
                instructions
                + "\n\nUse the following retrieved rulebook excerpts as your "
                "primary source. Cite rule sections (e.g., A6.4) from these "
                "excerpts in your answer.\n\n"
                + context_block
            )
        messages = [
            {"role": "system", "content": sys_with_context},
            {"role": "user", "content": question},
        ]

        # 3. Inference via OpenRouter.
        inference_start = time.time()
        response = self.openrouter_client.create_chat(
            model=model,
            messages=messages,
            stream=False,
            temperature=temperature,
        )
        inference_ms = (time.time() - inference_start) * 1000
        total_ms = retrieval_ms + inference_ms
        logging.info(
            f"[RAG Latency] OpenRouter inference: {inference_ms:.1f}ms · total {total_ms:.1f}ms"
        )

        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

        timing_data: Dict[str, Any] = {
            "retrieval_ms": round(retrieval_ms, 1),
            "inference_ms": round(inference_ms, 1),
            # Aliases to keep the existing UI / persistence layer working —
            # the latency-row JS reads file_search_time_ms for the RAG chip.
            "file_search_time_ms": round(retrieval_ms, 1),
            "ttft_ms": round(total_ms, 1),     # non-streaming: TTFT == TOTAL
            "total_time_ms": round(total_ms, 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "rag_chunks": len(chunks),
            # No file_citation metadata from OpenRouter; rule references will
            # still be clickable client-side via makeSectionReferencesClickable.
            "rag_sources": [],
        }

        if stream:
            def one_shot_generator():
                yield text
            if return_timing:
                return one_shot_generator(), timing_data
            return one_shot_generator(), []

        return text

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
            stripper = _CitationStripper()

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
                    
                    # Yield deltas immediately for true streaming (citation
                    # markers stripped on the way out).
                    if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                        delta = getattr(event, 'delta', None)
                        if delta:
                            cleaned = stripper.feed(delta)
                            if cleaned:
                                yield cleaned

                # Flush any whitespace held back by the stripper.
                tail = stripper.flush()
                if tail:
                    yield tail

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
    
    def _handle_agentic_streaming_response(
        self,
        input_data,
        instructions: str,
        model: str,
        temperature: Optional[float],
        tools: List[Dict[str, Any]],
        api_call_start_time: float,
        return_timing: bool,
        max_iterations: int = 5,
    ) -> Tuple[Generator[str, None, None], Any]:
        """
        Agentic answer that preserves streaming: resolve any tool calls, then
        stream the final answer.

        Each turn is streamed. Turns where the model calls a function carry no
        user-visible text (the model emits function_call items, not prose), so
        forwarding output_text deltas as they arrive yields a clean
        "tools resolve, then the answer streams" experience. After a turn, any
        function_call blocks are executed locally and their outputs submitted
        via previous_response_id; a turn with no function calls is the final
        answer.

        Returns (generator, timing_data) — same contract as
        _handle_streaming_response: timing_data fills in once the generator is
        fully consumed.
        """
        import json as json_module

        timing_data: Dict[str, Any] = {}

        def stream_generator():
            prev_id: Optional[str] = None
            current_input = input_data
            first_delta_time: Optional[float] = None
            file_search_complete_time: Optional[float] = None
            total_input_tokens = 0
            total_output_tokens = 0
            rag_sources: List[Dict[str, Any]] = []
            tools_called: List[str] = []

            for iteration in range(max_iterations):
                stream_manager = self.client.stream_response(
                    model=model,
                    input=current_input,
                    instructions=instructions,
                    temperature=temperature,
                    tools=tools,
                    previous_response_id=prev_id,
                )
                with stream_manager as stream:
                    for event in stream:
                        etype = getattr(event, "type", None)
                        if file_search_complete_time is None and etype == "response.file_search_call.completed":
                            file_search_complete_time = time.time()
                        if etype == "response.output_text.delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                if first_delta_time is None:
                                    first_delta_time = time.time()
                                yield delta
                    final = stream.get_final_response()

                prev_id = getattr(final, "id", None)
                usage = getattr(final, "usage", None)
                if usage:
                    total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                    total_output_tokens += getattr(usage, "output_tokens", 0) or 0
                output = getattr(final, "output", []) or []
                rag_sources.extend(_extract_rag_sources_from_output(output))

                calls = _output_function_calls(output)
                if not calls:
                    logging.info("🤖 Agentic(stream) finished after %d iteration(s)", iteration + 1)
                    break

                tools_called.extend(c["name"] for c in calls)
                logging.info(
                    "🔧 Agentic(stream) iter %d: executing %d tool call(s): %s",
                    iteration + 1, len(calls), [c["name"] for c in calls],
                )
                function_results = []
                for fc in calls:
                    try:
                        raw = fc.get("arguments")
                        args = json_module.loads(raw) if isinstance(raw, str) else (raw or {})
                        logging.info("  📞 %s(%s)", fc["name"], args)
                        output_json = json_module.dumps(execute_tool(fc["name"], args))
                    except Exception as e:
                        logging.error("  ❌ Tool error in %s: %s", fc.get("name"), e)
                        output_json = json_module.dumps({"error": str(e)})
                    function_results.append({
                        "type": "function_call_output",
                        "call_id": fc["call_id"],
                        "output": output_json,
                    })
                current_input = function_results
            else:
                logging.warning("⚠️ Agentic(stream) reached max_iterations=%d", max_iterations)

            if return_timing:
                stream_end_time = time.time()
                for i, r in enumerate(rag_sources, 1):
                    r["index"] = i
                timing_data.update({
                    "ttft_ms": round((first_delta_time - api_call_start_time) * 1000, 1) if first_delta_time else None,
                    "file_search_time_ms": round((file_search_complete_time - api_call_start_time) * 1000, 1) if file_search_complete_time else None,
                    "total_time_ms": round((stream_end_time - api_call_start_time) * 1000, 1),
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "rag_sources": rag_sources,
                    "tools_called": tools_called,
                })

        generator = stream_generator()
        if return_timing:
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
