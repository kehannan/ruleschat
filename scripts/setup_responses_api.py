#!/usr/bin/env python3
"""
Setup script for Responses API with vector store
This script supports versioned vector stores with section metadata chunking.

Versions:
- v1: Direct PDF upload (original method)
- v2: Section metadata chunking (new method with {A4.1} format)
"""

import os
import sys
import re
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import logging
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from typing import Dict, Any, List, Tuple
import pdfplumber

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Chunking parameters
MAX_CHUNK_SIZE = 4000
MIN_CHUNK_SIZE = 100
OVERLAP = 200
SMALL_SECTION_THRESHOLD = 200
MAX_SMALL_PER_CHUNK = 3


def load_existing_config(config_path: Path) -> Dict[str, Any]:
    """Load existing config file, migrating to versioned format if needed."""
    if not config_path.exists():
        return {"versions": {}, "active_version": None}
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        
        # Check if already in versioned format
        if "versions" in config:
            return config
        
        # Migrate old format to versioned format
        logging.info("📦 Migrating existing config to versioned format...")
        old_vector_store_id = config.get("vector_store_id")
        old_file_id = config.get("file_id")
        old_pdf_path = config.get("pdf_path")
        
        if old_vector_store_id:
            return {
                "versions": {
                    "v1": {
                        "vector_store_id": old_vector_store_id,
                        "file_id": old_file_id,
                        "pdf_path": old_pdf_path,
                        "chunking_method": "direct_pdf",
                        "created_at": datetime.now().isoformat()
                    }
                },
                "active_version": "v1"
            }
        else:
            return {"versions": {}, "active_version": None}
    except Exception as e:
        logging.error(f"Error loading config: {e}")
        return {"versions": {}, "active_version": None}


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF file using pdfplumber."""
    logging.info(f"📄 Extracting text from PDF: {pdf_path}")
    text_parts = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            logging.info(f"   Processing {total_pages} pages...")
            
            for i, page in enumerate(pdf.pages):
                if (i + 1) % 50 == 0:
                    logging.info(f"   Processed {i + 1}/{total_pages} pages...")
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        
        full_text = "\n".join(text_parts)
        logging.info(f"✅ Extracted {len(full_text):,} characters from PDF")
        return full_text
    except Exception as e:
        logging.error(f"❌ Error extracting text from PDF: {e}")
        raise


def parse_sections(text: str) -> List[Tuple[str, str]]:
    """
    Parse sections from text.
    Returns list of (section_id, content) tuples.
    """
    logging.info("🔍 Parsing sections from text...")
    
    # Pattern to match section headers: A4.1, A4.15, C8.1, etc.
    section_pattern = r'^([A-Z]\d+\.\d+(?:\.\d+)?)'
    
    matches = list(re.finditer(section_pattern, text, re.MULTILINE))
    sections = []
    
    for i, match in enumerate(matches):
        section_id = match.group(1)
        start_pos = match.start()
        # Get end position (next section or end of file)
        end_pos = matches[i+1].start() if i+1 < len(matches) else len(text)
        content = text[start_pos:end_pos]
        # Remove the section header from content
        content = content[len(section_id):].strip()
        sections.append((section_id, content))
    
    logging.info(f"✅ Found {len(sections)} sections")
    return sections


def format_chunks(sections: List[Tuple[str, str]]) -> List[str]:
    """
    Format sections into chunks with metadata.
    Returns list of formatted chunk strings.
    """
    logging.info("📝 Formatting chunks with section metadata...")
    
    chunks = []
    small_sections_buffer = []
    current_chunk_size = 0
    
    for section_id, content in sections:
        section_overhead = len(section_id) + 3  # "{A4.1} "
        content_size = len(content)
        effective_size = content_size + section_overhead
        
        # Handle small sections: combine up to 3 per chunk
        if content_size < SMALL_SECTION_THRESHOLD:
            if (current_chunk_size + effective_size <= MAX_CHUNK_SIZE and 
                len(small_sections_buffer) < MAX_SMALL_PER_CHUNK):
                small_sections_buffer.append((section_id, content))
                current_chunk_size += effective_size
            else:
                # Flush buffer and start new chunk
                if small_sections_buffer:
                    chunk_parts = [f"{{{sid}}} {cont}" for sid, cont in small_sections_buffer]
                    chunks.append(" ".join(chunk_parts))
                small_sections_buffer = [(section_id, content)]
                current_chunk_size = effective_size
        else:
            # Flush any pending small sections
            if small_sections_buffer:
                chunk_parts = [f"{{{sid}}} {cont}" for sid, cont in small_sections_buffer]
                chunks.append(" ".join(chunk_parts))
                small_sections_buffer = []
                current_chunk_size = 0
            
            # Handle medium/large sections
            if effective_size <= MAX_CHUNK_SIZE:
                # Single chunk
                chunks.append(f"{{{section_id}}} {content}")
            else:
                # Split large section with overlap
                section_chunks = split_large_section(section_id, content)
                chunks.extend(section_chunks)
    
    # Don't forget any remaining small sections
    if small_sections_buffer:
        chunk_parts = [f"{{{sid}}} {cont}" for sid, cont in small_sections_buffer]
        chunks.append(" ".join(chunk_parts))
    
    logging.info(f"✅ Created {len(chunks)} chunks from {len(sections)} sections")
    return chunks


def split_large_section(section_id: str, content: str) -> List[str]:
    """Split a large section into multiple chunks with overlap."""
    section_overhead = len(section_id) + 3  # "{A4.1} "
    effective_chunk_size = MAX_CHUNK_SIZE - OVERLAP - section_overhead
    chunks = []
    
    start = 0
    while start < len(content):
        # Calculate end position
        end = start + effective_chunk_size
        
        # Try to break at sentence boundary if possible
        if end < len(content):
            # Look for sentence endings near the end
            for i in range(end, max(start + effective_chunk_size - 500, start), -1):
                if content[i] in '.!?' and (i == len(content) - 1 or content[i+1] in ' \n'):
                    end = i + 1
                    break
        
        chunk_content = content[start:end]
        chunks.append(f"{{{section_id}}} {chunk_content}")
        
        # Move start position with overlap
        start = end - OVERLAP
        if start >= len(content):
            break
    
    return chunks


def create_chunked_text_file(chunks: List[str], output_path: str):
    """Write formatted chunks to a text file."""
    logging.info(f"💾 Writing {len(chunks)} chunks to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(chunk)
            f.write("\n\n")  # Double newline between chunks
    logging.info(f"✅ Chunked text file created: {output_path}")


def setup_asl_vector_store_v2(client, pdf_path: str) -> Dict[str, Any]:
    """Set up ASL vector store v2 with section metadata chunking."""
    logging.info("🚀 Setting up ASL Vector Store v2 (Section Metadata Chunking)...")
    
    try:
        # Step 1: Extract text from PDF
        text = extract_text_from_pdf(pdf_path)
        
        # Step 2: Parse sections
        sections = parse_sections(text)
        
        # Step 3: Format chunks with section metadata
        chunks = format_chunks(sections)
        
        # Step 4: Create temporary text file with chunks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_file:
            tmp_path = tmp_file.name
            create_chunked_text_file(chunks, tmp_path)
        
        try:
            # Step 5: Create new vector store
            vector_store_id = create_vector_store(client, "ASL Rules Vector Store v2 (Section Metadata)")
            
            # Step 6: Upload processed text file
            file_id = upload_file_to_vector_store_and_wait(client, tmp_path, vector_store_id)
            
            return {
                "vector_store_id": vector_store_id,
                "file_id": file_id,
                "pdf_path": pdf_path,
                "chunking_method": "section_metadata",
                "chunk_size": MAX_CHUNK_SIZE,
                "overlap": OVERLAP,
                "total_chunks": len(chunks),
                "total_sections": len(sections),
                "created_at": datetime.now().isoformat()
            }
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except:
                pass
                
    except Exception as e:
        logging.error(f"❌ Error in setup: {e}")
        raise


def setup_asl_vector_store_v1(client, pdf_path: str) -> Dict[str, Any]:
    """Set up ASL vector store v1 (direct PDF upload - original method)."""
    logging.info("🚀 Setting up ASL Vector Store v1 (Direct PDF Upload)...")
    try:
        # Create vector store
        vector_store_id = create_vector_store(client, "ASL Rules Vector Store v1")
        
        # Upload PDF to vector store and wait for it to be ready
        file_id = upload_file_to_vector_store_and_wait(client, pdf_path, vector_store_id)
        
        return {
            "vector_store_id": vector_store_id,
            "file_id": file_id,
            "pdf_path": pdf_path,
            "chunking_method": "direct_pdf",
            "created_at": datetime.now().isoformat()
        }
    except Exception as e:
        logging.error(f"❌ Error in setup: {e}")
        raise


def upload_file_to_vector_store_and_wait(client, file_path: str, vector_store_id: str) -> str:
    """Upload a file, add it to the vector store, and wait for it to be ready."""
    if not vector_store_id:
        raise ValueError("Vector store ID is required")
        
    try:
        logging.info(f"📤 Uploading file '{file_path}' to OpenAI...")
        with open(file_path, 'rb') as file:
            file_response = client.files.create(
                file=file,
                purpose="assistants"
            )
        logging.info(f"✅ File uploaded to OpenAI with ID: {file_response.id}")

        logging.info(f"➕ Attaching file {file_response.id} to vector store {vector_store_id}...")
        vector_store_file = client.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=file_response.id
        )
        logging.info(f"✅ File attached to vector store.")

        logging.info(f"⏳ Waiting for file to be processed...")
        while True:
            vector_store_file = client.vector_stores.files.retrieve(
                vector_store_id=vector_store_id,
                file_id=file_response.id
            )
            if vector_store_file.status == 'completed':
                logging.info(f"✅ File processing complete.")
                break
            elif vector_store_file.status in ['failed', 'cancelled']:
                raise Exception(f"File processing failed with status: {vector_store_file.status}")
            
            logging.info(f"   Current status: {vector_store_file.status}... waiting 10 seconds.")
            time.sleep(10)
            
        return file_response.id
    except Exception as e:
        logging.error(f"❌ Error during file upload and processing: {e}")
        raise


def create_vector_store(client, name: str = "ASL Rules Vector Store") -> str:
    """Create a vector store for ASL rules documents"""
    logging.info(f"📚 Creating vector store: {name}...")
    try:
        response = client.vector_stores.create(
            name=name,
            expires_after={"anchor": "last_active_at", "days": 365}
        )
        logging.info(f"✅ Created vector store: {response.id}")
        return response.id
    except Exception as e:
        logging.error(f"❌ Error creating vector store: {e}")
        raise


def test_responses_api(client, vector_store_id: str):
    """Test the Responses API with a sample query"""
    try:
        from app.config import DEFAULT_MODEL, ASL_SYSTEM_INSTRUCTIONS
        
        response = client.responses.create(
            model=DEFAULT_MODEL,
            input="What are the basic rules for movement in ASL?",
            instructions=ASL_SYSTEM_INSTRUCTIONS,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
            }]
        )
        
        if response.output_text:
            logging.info("✅ Test successful!")
            logging.info(f"📝 Response preview: {response.output_text[:200]}...")
        else:
            logging.warning("⚠️ No response received from test query.")
            
    except Exception as test_error:
        logging.error(f"❌ Test failed: {test_error}")


def main():
    """Main function to set up vector store"""
    logging.info("🚀 Initializing Vector Store Setup...")
    
    # Load environment variables
    load_dotenv()
    
    try:
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in .env file")
        
        client = OpenAI(
            api_key=api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        # PDF file path - now in the evals-sft repository
        pdf_path = "../mysite2-evals-sft/rulebook/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
        
        # Load existing config
        config_path = Path("responses_api_config.json")
        config = load_existing_config(config_path)
        
        # Set up v2 vector store (section metadata chunking)
        logging.info("\n" + "="*60)
        logging.info("Creating v2 vector store with section metadata...")
        logging.info("="*60)
        v2_data = setup_asl_vector_store_v2(client, pdf_path)
        
        # Update config with v2
        config["versions"]["v2"] = v2_data
        config["active_version"] = "v2"
        
        # Save configuration to file
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        
        logging.info(f"\n💾 Configuration saved to {config_path}")
        logging.info(f"   Active version: v2")
        logging.info(f"   Total versions: {len(config['versions'])}")
        
        # Test the API
        logging.info("\n🧪 Testing the setup with Responses API...")
        test_responses_api(client, v2_data["vector_store_id"])
        
    except Exception as e:
        logging.error(f"Setup error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
