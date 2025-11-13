"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""

import os
import json
import logging
import time
from typing import Optional
from openai import OpenAI
from pathlib import Path


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
        # Load configuration
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        # Load vector store ID
        if vector_store_id:
            self.vector_store_id = vector_store_id
        else:
            self.vector_store_id = self._load_vector_store_id(config_file)
        
        if not self.vector_store_id:
            raise ValueError("Vector store ID is required. Please configure responses_api_config.json")
        
        # Import config from app.config (ensures consistency)
        from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE
        
        self.model = DEFAULT_MODEL
        self.temperature = TEMPERATURE
        self.system_instructions = ASL_SYSTEM_INSTRUCTIONS
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=self.api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        logging.info(f"ASL Service initialized with vector store: {self.vector_store_id}")
    
    def _load_vector_store_id(self, config_file: Optional[str] = None) -> Optional[str]:
        """Load vector store ID from config file (supports versioned config)."""
        if config_file:
            config_path = Path(config_file)
        else:
            # Default location: responses_api_config.json in project root
            config_path = Path(__file__).parent.parent.parent / "responses_api_config.json"
        
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    
                    # Check if versioned config format
                    if "versions" in config:
                        active_version = config.get("active_version")
                        if active_version and active_version in config["versions"]:
                            version_data = config["versions"][active_version]
                            vector_store_id = version_data.get("vector_store_id")
                            logging.info(f"Loaded vector store ID from versioned config (active: {active_version})")
                            return vector_store_id
                        else:
                            logging.warning(f"Active version '{active_version}' not found in config")
                            return None
                    else:
                        # Legacy format (backward compatibility)
                        return config.get("vector_store_id")
            except Exception as e:
                logging.error(f"Error loading config file: {e}")
        else:
            logging.warning(f"Config file not found: {config_path}")
        
        return None
    
    def get_answer(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        return_timing: bool = False,
        force_web_search: bool = False
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
            
        Returns:
            The answer as a string (or generator if stream=True)
            If return_timing=True and stream=True, returns (generator, timing_data)
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        
        # Modify instructions if web search is forced
        instructions = self.system_instructions
        if force_web_search:
            instructions = self.system_instructions + "\n\nIMPORTANT: The user has requested web search. You MUST use web_search to find current information, community discussions, and recent clarifications. Also use file_search to reference the rulebook. Use both tools together to provide a comprehensive answer."
        
        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            # Use Responses API with file_search and web_search tools
            response = self.client.responses.create(
                model=model,
                input=question,
                instructions=instructions,
                temperature=temperature,
                stream=stream,
                tools=[
                    {
                        "type": "file_search",
                        "vector_store_ids": [self.vector_store_id],
                    },
                    {
                        "type": "web_search",
                    }
                ]
            )
            
            if stream:
                # For streaming, we need to collect events first to log chunks
                # then replay them for the actual stream
                events = []
                output_text = ""
                
                # Timing variables
                first_event_time = None
                file_search_complete_time = None
                first_delta_time = None
                stream_end_time = None
                
                for event in response:
                    # Track first event (could be various types - log what it actually is)
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
                first_event_ms = (first_event_time - api_call_start_time) * 1000 if first_event_time else None
                file_search_complete_ms = (file_search_complete_time - api_call_start_time) * 1000 if file_search_complete_time else None
                first_token_ms = (first_delta_time - api_call_start_time) * 1000 if first_delta_time else None
                generation_time_ms = (stream_end_time - file_search_complete_time) * 1000 if file_search_complete_time else None
                rag_time_ms = file_search_complete_ms if file_search_complete_ms else None
                
                # Log latency summary
                logging.info(f"[RAG Latency] Total streaming time: {total_streaming_time_ms:.1f}ms")
                if file_search_complete_time:
                    logging.info(f"[RAG Latency] Generation time (after RAG): {generation_time_ms:.1f}ms")
                if first_delta_time:
                    rag_to_first_token_ms = (first_delta_time - api_call_start_time) * 1000
                    logging.info(f"[RAG Latency] RAG + initial generation: {rag_to_first_token_ms:.1f}ms")
                
                # Prepare timing data
                timing_data = {
                    "api_call_start": api_call_start_time,
                    "first_event_ms": first_event_ms,
                    "file_search_complete_ms": file_search_complete_ms,
                    "first_token_ms": first_token_ms,
                    "stream_end_ms": total_streaming_time_ms,
                    "total_ms": total_streaming_time_ms,
                    "rag_time_ms": rag_time_ms,
                    "generation_time_ms": generation_time_ms
                }
                
                # Log retrieved chunks from collected events
                # Vector store logging disabled
                # self._log_retrieved_chunks_streaming(events, output_text, question)
                
                # Return generator that replays the events
                def stream_generator():
                    for event in events:
                        if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                            if hasattr(event, 'delta') and event.delta:
                                yield event.delta
                
                if return_timing:
                    return stream_generator(), timing_data
                return stream_generator()
            else:
                # Non-streaming mode - measure total response time
                response_start_time = time.time()
                response_text = self._extract_response_text(response)
                response_end_time = time.time()
                
                total_time_ms = (response_end_time - api_call_start_time) * 1000
                logging.info(f"[RAG Latency] Total response time (non-streaming): {total_time_ms:.1f}ms")
                
                # Log retrieved chunks (for debugging)
                # Vector store logging disabled
                # self._log_retrieved_chunks(response, question, stream)
                # Return complete response
                return response_text
                
        except Exception as e:
            error_msg = f"Error getting response: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def _extract_response_text(self, response) -> str:
        """Extract text from response object."""
        if hasattr(response, 'output_text') and response.output_text:
            return response.output_text
        elif hasattr(response, 'output') and response.output:
            if isinstance(response.output, str):
                return response.output
            elif isinstance(response.output, dict) and 'text' in response.output:
                return response.output['text']
        
        return "No response content found"
    
    def _log_retrieved_chunks_streaming(self, events, output_text: str, question: str):
        """
        Log retrieved chunks from streaming response events.
        """
        import re
        import json
        
        logging.info("=" * 80)
        logging.info(f"🔍 RAG CHUNK DEBUG (STREAMING) for question: {question}")
        logging.info("=" * 80)
        
        # Log all event types to see what's available
        event_types = {}
        for event in events:
            event_type = getattr(event, 'type', 'unknown')
            if event_type not in event_types:
                event_types[event_type] = []
            event_types[event_type].append(event)
        
        logging.info(f"📦 Event types found: {list(event_types.keys())}")
        
        # Check for citation/file_search events and extract raw chunks
        citations_found = False
        raw_chunks = []
        
        for event in events:
            event_type = getattr(event, 'type', 'unknown')
            
            # Extract annotation data (contains citations)
            if event_type == 'response.output_text.annotation.added':
                logging.info(f"📎 Annotation event found - extracting citation data...")
                # Try to get annotation data
                if hasattr(event, 'annotation'):
                    annotation = event.annotation
                    logging.info(f"   Annotation type: {type(annotation)}")
                    # Try to serialize it
                    try:
                        if hasattr(annotation, '__dict__'):
                            logging.info(f"   Annotation dict: {json.dumps(annotation.__dict__, default=str, indent=2)[:1000]}")
                        else:
                            logging.info(f"   Annotation: {str(annotation)[:500]}")
                    except Exception as e:
                        logging.info(f"   Could not serialize annotation: {e}")
                
                # Check for citations in the annotation
                if hasattr(event, 'citations'):
                    citations_found = True
                    logging.info(f"📎 Citations in annotation: {event.citations}")
            
            # Extract file_search results
            if event_type == 'response.file_search_call.completed':
                logging.info(f"🔍 File search completed - extracting results...")
                if hasattr(event, 'file_search_call'):
                    fs_call = event.file_search_call
                    logging.info(f"   File search call type: {type(fs_call)}")
                    try:
                        if hasattr(fs_call, '__dict__'):
                            logging.info(f"   File search dict: {json.dumps(fs_call.__dict__, default=str, indent=2)[:1000]}")
                        if hasattr(fs_call, 'results'):
                            logging.info(f"   Results: {fs_call.results}")
                        if hasattr(fs_call, 'chunks'):
                            raw_chunks = fs_call.chunks
                            logging.info(f"   Found {len(raw_chunks)} raw chunks!")
                    except Exception as e:
                        logging.info(f"   Could not extract file search data: {e}")
            
            # Log other important events
            if event_type not in ['response.output_text.delta', 'response.output_text.annotation.added', 'response.file_search_call.completed']:
                # Try to get useful attributes
                for attr in ['citations', 'file_search', 'data', 'content', 'text', 'chunks', 'results']:
                    if hasattr(event, attr):
                        value = getattr(event, attr)
                        if value:
                            try:
                                if isinstance(value, (dict, list)):
                                    logging.info(f"   {event_type}.{attr}: {json.dumps(value, default=str, indent=2)[:500]}")
                                else:
                                    logging.info(f"   {event_type}.{attr}: {str(value)[:500]}")
                            except:
                                logging.info(f"   {event_type}.{attr}: <non-serializable>")
        
        # Log raw chunks if we found them
        if raw_chunks:
            logging.info("=" * 80)
            logging.info(f"📋 RAW CHUNKS FROM FILE SEARCH ({len(raw_chunks)} chunks):")
            logging.info("=" * 80)
            for i, chunk in enumerate(raw_chunks[:5]):  # Show first 5 chunks
                try:
                    chunk_str = json.dumps(chunk, default=str, indent=2) if isinstance(chunk, (dict, list)) else str(chunk)
                    logging.info(f"Chunk {i+1}: {chunk_str[:500]}")
                except:
                    logging.info(f"Chunk {i+1}: {str(chunk)[:500]}")
            logging.info("=" * 80)
        
        # Make a separate non-streaming call to get raw chunks with better instructions
        # First, extract section references from the output to query for specific sections
        section_refs = re.findall(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b', output_text)
        unique_sections = list(set(section_refs))
        
        try:
            logging.info("🔍 Making diagnostic query to see raw chunks...")
            if unique_sections:
                # Query for the specific section(s) mentioned
                section_query = f"Show me the EXACT raw chunk for section {unique_sections[0]}. Include the full metadata format at the start like {{section|page}} or {{section}}. Copy the entire chunk verbatim starting from the metadata."
                logging.info(f"   Querying for section: {unique_sections[0]}")
            else:
                section_query = f"Question: {question}\n\nShow me the EXACT raw text chunks. Include ALL metadata in the format {{section|page}} or {{section}} at the beginning of each chunk."
            
            diagnostic_response = self.client.responses.create(
                model=self.model,
                input=section_query,
                instructions="You are a diagnostic tool. Show the EXACT raw chunks from the vector store. Each chunk starts with metadata like {A7.36|48} or {A7.36} followed by a space, then the content. Copy the ENTIRE chunk including the metadata prefix. Do NOT reformat, paraphrase, or modify. Show the raw format exactly as stored.",
                temperature=0.0,
                stream=False,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [self.vector_store_id],
                }]
            )
            
            if hasattr(diagnostic_response, 'output_text') and diagnostic_response.output_text:
                raw_output = diagnostic_response.output_text
                logging.info("=" * 80)
                logging.info("📋 RAW VECTOR STORE CHUNKS (from diagnostic query):")
                logging.info("=" * 80)
                logging.info(raw_output)
                logging.info("=" * 80)
                
                # Extract all metadata patterns
                all_metadata = re.findall(r'\{[^}]+\}', raw_output)
                if all_metadata:
                    logging.info(f"📦 Found {len(all_metadata)} metadata patterns in raw chunks:")
                    for meta in all_metadata[:20]:  # Show first 20
                        logging.info(f"   {meta}")
                
                # Check for section|page format
                chunk_pattern = re.compile(r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\|(\d+)\}')
                matches = chunk_pattern.findall(raw_output)
                if matches:
                    logging.info(f"✅ Found {len(matches)} section|page references in RAW chunks:")
                    for section, page in matches:
                        logging.info(f"   - Section: {section}, Page: {page}")
                else:
                    # Check for just section format
                    section_only_pattern = re.compile(r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\}')
                    section_matches = section_only_pattern.findall(raw_output)
                    if section_matches:
                        logging.info(f"⚠️ Found {len(section_matches)} section-only references (no page numbers - using v2?):")
                        for section in set(section_matches[:10]):
                            logging.info(f"   - Section: {section}")
        except Exception as e:
            logging.warning(f"⚠️ Could not make diagnostic query: {e}")
            import traceback
            logging.warning(traceback.format_exc())
        
        # Extract chunks from actual output text (look for {section|page} format)
        chunk_pattern = re.compile(r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\|(\d+)\}')
        matches = chunk_pattern.findall(output_text)
        if matches:
            logging.info(f"📄 Found {len(matches)} section|page references in AI output:")
            for section, page in matches:
                logging.info(f"   - Section: {section}, Page: {page}")
        else:
            logging.warning("⚠️ No {section|page} format found in AI output text")
        
        # Also look for section references without page numbers
        section_pattern = re.compile(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b')
        section_matches = section_pattern.findall(output_text)
        unique_sections = set(section_matches)
        if unique_sections:
            logging.info(f"📋 Found {len(unique_sections)} unique section references in AI output: {sorted(unique_sections)}")
        
        # Show sample of output text to see what format chunks are in
        if output_text:
            logging.info(f"📝 AI Output text sample (first 1000 chars):")
            logging.info(f"   {output_text[:1000]}")
            
            # Try to find any {section} or {section|page} patterns
            all_patterns = re.findall(r'\{[^}]+\}', output_text[:2000])
            if all_patterns:
                logging.info(f"📦 Found {len(all_patterns)} metadata patterns in AI output: {all_patterns[:10]}")
        
        logging.info("=" * 80)
    
    def _log_retrieved_chunks(self, response, question: str, is_streaming: bool):
        """
        Log the retrieved RAG chunks with section, page, and content.
        This helps debug page number issues.
        """
        import re
        
        logging.info("=" * 80)
        logging.info(f"🔍 RAG CHUNK DEBUG for question: {question}")
        logging.info("=" * 80)
        
        # For streaming responses, we need to collect the full response
        # For non-streaming, we can inspect directly
        if is_streaming:
            # Collect all events to see citations
            events = []
            output_text = ""
            for event in response:
                events.append(event)
                if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                    if hasattr(event, 'delta') and event.delta:
                        output_text += event.delta
                # Check for citation events
                if hasattr(event, 'type') and 'citation' in event.type.lower():
                    logging.info(f"📎 Citation event: {event}")
            
            # Try to find citations in events
            citations_found = False
            for event in events:
                if hasattr(event, 'citations'):
                    citations_found = True
                    logging.info(f"📎 Citations found: {event.citations}")
                if hasattr(event, 'file_search'):
                    logging.info(f"🔍 File search results: {event.file_search}")
            
            # Extract chunks from output text (look for {section|page} format)
            chunk_pattern = re.compile(r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\|(\d+)\}')
            matches = chunk_pattern.findall(output_text)
            if matches:
                logging.info(f"📄 Found {len(matches)} section|page references in output:")
                for section, page in matches:
                    logging.info(f"   - Section: {section}, Page: {page}")
            
            # Also look for section references without page numbers
            section_pattern = re.compile(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b')
            section_matches = section_pattern.findall(output_text)
            unique_sections = set(section_matches)
            if unique_sections:
                logging.info(f"📋 Found {len(unique_sections)} unique section references: {sorted(unique_sections)}")
            
            if not citations_found and not matches:
                logging.warning("⚠️ No citations or section|page metadata found in streaming response")
                logging.info(f"📝 Full output text (first 500 chars): {output_text[:500]}")
        else:
            # Non-streaming response - inspect directly
            if hasattr(response, 'citations'):
                logging.info(f"📎 Citations: {response.citations}")
            if hasattr(response, 'output_text') and response.output_text:
                # Extract chunks from output text
                chunk_pattern = re.compile(r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\|(\d+)\}')
                matches = chunk_pattern.findall(response.output_text)
                if matches:
                    logging.info(f"📄 Found {len(matches)} section|page references in output:")
                    for section, page in matches:
                        logging.info(f"   - Section: {section}, Page: {page}")
                
                # Also look for section references
                section_pattern = re.compile(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b')
                section_matches = section_pattern.findall(response.output_text)
                unique_sections = set(section_matches)
                if unique_sections:
                    logging.info(f"📋 Found {len(unique_sections)} unique section references: {sorted(unique_sections)}")
            
            # Try to access file_search results if available
            if hasattr(response, 'file_search_results'):
                logging.info(f"🔍 File search results: {response.file_search_results}")
            
            # Log response object attributes for debugging
            logging.info(f"📦 Response object attributes: {dir(response)}")
            for attr in ['citations', 'file_search', 'output', 'output_text']:
                if hasattr(response, attr):
                    value = getattr(response, attr)
                    if value:
                        logging.info(f"   - {attr}: {str(value)[:200]}")
        
        logging.info("=" * 80)


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

