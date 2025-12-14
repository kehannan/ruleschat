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


def extract_text_two_column(page) -> str:
    """
    Extract text from a two-column PDF page using word boxes.
    
    This approach:
    1. Extracts individual word boxes with their positions
    2. Splits words into left/right columns based on x-position
    3. Reconstructs lines within each column by grouping words at similar y-positions
    4. Joins left column text, then right column text
    
    This avoids the interleaving and word truncation issues of simple extract_text().
    """
    try:
        words = page.extract_words()
        if not words:
            return page.extract_text() or ""
        
        page_width = float(page.width)
        mid_x = page_width / 2
        
        # Split words into left and right columns
        left_words = [w for w in words if float(w['x0']) < mid_x]
        right_words = [w for w in words if float(w['x0']) >= mid_x]
        
        def reconstruct_text(word_list):
            """Reconstruct text from word boxes, grouping by line (y-position)."""
            if not word_list:
                return ""
            
            # Sort by y-position (top), then x-position
            sorted_words = sorted(word_list, key=lambda w: (float(w['top']), float(w['x0'])))
            
            # Group words into lines (words within ~5 points of each other vertically)
            lines = []
            current_line = []
            current_y = None
            line_threshold = 5.0  # pixels tolerance for same line
            
            for word in sorted_words:
                word_y = float(word['top'])
                
                if current_y is None:
                    current_y = word_y
                    current_line = [word]
                elif abs(word_y - current_y) <= line_threshold:
                    # Same line
                    current_line.append(word)
                else:
                    # New line - save current and start new
                    if current_line:
                        # Sort words in line by x-position
                        current_line.sort(key=lambda w: float(w['x0']))
                        line_text = ' '.join(w['text'] for w in current_line)
                        lines.append(line_text)
                    current_line = [word]
                    current_y = word_y
            
            # Don't forget the last line
            if current_line:
                current_line.sort(key=lambda w: float(w['x0']))
                line_text = ' '.join(w['text'] for w in current_line)
                lines.append(line_text)
            
            return '\n'.join(lines)
        
        left_text = reconstruct_text(left_words)
        right_text = reconstruct_text(right_words)
        
        # Combine: left column first, then right column
        combined = left_text.strip()
        if right_text.strip():
            combined += "\n\n" + right_text.strip()
        
        # If result is too short, fall back to simple extraction
        if len(combined) < 50:
            return page.extract_text() or ""
        
        return combined
        
    except Exception as e:
        logging.warning(f"⚠️ Word extraction failed, falling back to simple: {e}")
        return page.extract_text() or ""


def extract_text_from_pdf(pdf_path: str) -> List[Tuple[int, str]]:
    """
    Extract text from PDF file using pdfplumber with page numbers.
    Uses two-column extraction for better handling of ASL rulebook layout.
    Returns list of (page_num, text) tuples.
    """
    logging.info(f"📄 Extracting text from PDF: {pdf_path}")
    logging.info(f"   Using two-column word-based extraction...")
    page_texts = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            logging.info(f"   Processing {total_pages} pages...")
            
            for i, page in enumerate(pdf.pages):
                if (i + 1) % 50 == 0:
                    logging.info(f"   Processed {i + 1}/{total_pages} pages...")
                # Use two-column extraction for ASL rulebook
                text = extract_text_two_column(page)
                if text:
                    page_texts.append((i + 1, text))  # Page numbers are 1-based
        
        total_chars = sum(len(text) for _, text in page_texts)
        logging.info(f"✅ Extracted {total_chars:,} characters from {len(page_texts)} pages")
        return page_texts
    except Exception as e:
        logging.error(f"❌ Error extracting text from PDF: {e}")
        raise


def parse_sections(page_texts: List[Tuple[int, str]]) -> List[Tuple[str, str, int]]:
    """
    Parse sections from page texts with page numbers.
    Returns list of (section_id, content, page_num) tuples.
    
    This function processes pages individually to correctly associate
    sections with their page numbers, then combines sections that span
    multiple pages.
    """
    logging.info("🔍 Parsing sections from text with page numbers...")
    
    # Pattern to match section headers: A4.1, A4.15, C8.1, etc.
    section_pattern = re.compile(r'^([A-Z]\d+\.\d+(?:\.\d+)?)', re.MULTILINE)
    
    # First pass: find all sections with their page numbers
    section_data = {}  # section_id -> (start_page, start_pos_in_page, content_parts)
    
    for page_num, text in page_texts:
        matches = list(section_pattern.finditer(text))
        
        for match in matches:
            section_id = match.group(1)
            start_pos = match.start()
            
            if section_id not in section_data:
                # First occurrence of this section - start tracking it
                section_data[section_id] = {
                    'start_page': page_num,
                    'start_pos': start_pos,
                    'content_parts': []
                }
            
            # Extract content from this page
            # Find where this section ends (next section or end of page)
            section_end = len(text)
            for other_match in matches:
                if other_match.start() > match.start():
                    section_end = other_match.start()
                    break
            
            page_content = text[match.start():section_end]
            # Remove the section header from content
            page_content = page_content[len(section_id):].strip()
            
            section_data[section_id]['content_parts'].append((page_num, page_content))
    
    # Second pass: combine content parts and determine final page number
    sections = []
    sections_without_page = 0
    for section_id, data in sorted(section_data.items()):
        # The section starts on the first page where it appears
        start_page = data['start_page']
        
        # Validate page number
        if not start_page or start_page < 1:
            logging.warning(f"⚠️ Section {section_id} has invalid page number: {start_page}, defaulting to 1")
            start_page = 1
            sections_without_page += 1
        
        # Combine all content parts
        content_parts = []
        for page_num, page_content in data['content_parts']:
            if page_content:
                content_parts.append(page_content)
        
        full_content = " ".join(content_parts).strip()
        
        if full_content:  # Only add if there's actual content
            sections.append((section_id, full_content, start_page))
    
    if sections_without_page > 0:
        logging.warning(f"⚠️ {sections_without_page} sections had invalid page numbers")
    
    # Log sample of sections with their page numbers
    if sections:
        logging.info(f"📄 Sample sections with page numbers:")
        for section_id, content, page_num in sections[:5]:
            logging.info(f"   {section_id} -> page {page_num} (content length: {len(content)})")
    
    logging.info(f"✅ Found {len(sections)} sections")
    return sections


def format_chunks(sections: List[Tuple[str, str, int]]) -> List[str]:
    """
    Format sections into chunks with section and page metadata.
    Returns list of formatted chunk strings with format: {A4.1|48} content
    """
    logging.info("📝 Formatting chunks with section and page metadata...")
    
    chunks = []
    small_sections_buffer = []
    current_chunk_size = 0
    
    for section_id, content, page_num in sections:
        # Validate page number
        if not page_num or page_num < 1:
            logging.warning(f"⚠️ Section {section_id} has invalid page number {page_num}, using page 1")
            page_num = 1
        
        # Format: {A4.1|48} - section ID and page number
        section_overhead = len(section_id) + len(str(page_num)) + 4  # "{A4.1|48} "
        content_size = len(content)
        effective_size = content_size + section_overhead
        
        # Handle small sections: combine up to 3 per chunk
        # Use the first section's page number for combined chunks
        if content_size < SMALL_SECTION_THRESHOLD:
            if (current_chunk_size + effective_size <= MAX_CHUNK_SIZE and 
                len(small_sections_buffer) < MAX_SMALL_PER_CHUNK):
                small_sections_buffer.append((section_id, content, page_num))
                current_chunk_size += effective_size
            else:
                # Flush buffer and start new chunk
                if small_sections_buffer:
                    chunk_parts = [f"{{{sid}|{pnum}}} {cont}" for sid, cont, pnum in small_sections_buffer]
                    chunks.append(" ".join(chunk_parts))
                small_sections_buffer = [(section_id, content, page_num)]
                current_chunk_size = effective_size
        else:
            # Flush any pending small sections
            if small_sections_buffer:
                chunk_parts = [f"{{{sid}|{pnum}}} {cont}" for sid, cont, pnum in small_sections_buffer]
                chunks.append(" ".join(chunk_parts))
                small_sections_buffer = []
                current_chunk_size = 0
            
            # Handle medium/large sections
            if effective_size <= MAX_CHUNK_SIZE:
                # Single chunk
                chunks.append(f"{{{section_id}|{page_num}}} {content}")
            else:
                # Split large section with overlap
                # All chunks from a split section use the same page number
                section_chunks = split_large_section(section_id, content, page_num)
                chunks.extend(section_chunks)
    
    # Don't forget any remaining small sections
    if small_sections_buffer:
        chunk_parts = [f"{{{sid}|{pnum}}} {cont}" for sid, cont, pnum in small_sections_buffer]
        chunks.append(" ".join(chunk_parts))
    
    logging.info(f"✅ Created {len(chunks)} chunks from {len(sections)} sections")
    return chunks


def split_large_section(section_id: str, content: str, page_num: int) -> List[str]:
    """Split a large section into multiple chunks with overlap."""
    section_overhead = len(section_id) + len(str(page_num)) + 4  # "{A4.1|48} "
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
        chunks.append(f"{{{section_id}|{page_num}}} {chunk_content}")
        
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


def simple_chunk_text(text: str, chunk_size: int = 4000, overlap: int = 200) -> List[str]:
    """
    Simple chunking: split text into fixed-size chunks with overlap.
    No metadata, no section parsing - just plain text chunks.
    """
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        
        # Move start position forward by chunk_size - overlap
        start += chunk_size - overlap
        
        # Don't go past the end
        if start >= text_length:
            break
    
    return chunks


def setup_asl_vector_store(client, pdf_path: str, version: str = "v5") -> Dict[str, Any]:
    """
    Set up ASL vector store with two-column word-based extraction.
    
    Args:
        client: OpenAI client
        pdf_path: Path to the PDF file
        version: Version label for the vector store (e.g., "v5", "v6")
    
    Features:
    - Uses word-box extraction for proper two-column handling
    - Avoids word truncation at column boundaries
    - Preserves reading order (left column then right column)
    """
    logging.info(f"🚀 Setting up ASL Vector Store {version} (Two-Column Word Extraction)...")
    
    try:
        # Step 1: Extract all text from PDF using two-column word extraction
        logging.info("📄 Extracting text from PDF...")
        logging.info("   Using two-column word-based extraction...")
        all_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            logging.info(f"   Processing {total_pages} pages...")
            
            for i, page in enumerate(pdf.pages):
                if (i + 1) % 50 == 0:
                    logging.info(f"   Processed {i + 1}/{total_pages} pages...")
                text = extract_text_two_column(page)
                if text:
                    all_text += text + "\n\n"
        
        logging.info(f"✅ Extracted {len(all_text):,} characters of text")
        
        # Step 2: Fixed-size chunking with overlap
        logging.info("📝 Creating fixed-size chunks...")
        chunks = simple_chunk_text(all_text, chunk_size=MAX_CHUNK_SIZE, overlap=OVERLAP)
        logging.info(f"✅ Created {len(chunks)} chunks")
        
        # Step 3: Create temporary text file with chunks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_file:
            tmp_path = tmp_file.name
            create_chunked_text_file(chunks, tmp_path)
        
        try:
            # Step 4: Create new vector store
            store_name = f"ASL Rules Vector Store {version} (Two-Column Word Extraction)"
            vector_store_id = create_vector_store(client, store_name)
            
            # Step 5: Upload processed text file
            file_id = upload_file_to_vector_store_and_wait(client, tmp_path, vector_store_id)
            
            return {
                "vector_store_id": vector_store_id,
                "file_id": file_id,
                "pdf_path": pdf_path,
                "chunking_method": "two_column_word_extraction",
                "chunk_size": MAX_CHUNK_SIZE,
                "overlap": OVERLAP,
                "total_chunks": len(chunks),
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


# Legacy v3 function kept for reference
def setup_asl_vector_store_v3(client, pdf_path: str) -> Dict[str, Any]:
    """Set up ASL vector store v3 with section and page metadata chunking."""
    logging.info("🚀 Setting up ASL Vector Store v3 (Section + Page Metadata Chunking)...")
    
    try:
        # Step 1: Extract text from PDF with page numbers
        page_texts = extract_text_from_pdf(pdf_path)
        
        # Step 2: Parse sections with page numbers
        sections = parse_sections(page_texts)
        
        # Step 3: Format chunks with section and page metadata
        chunks = format_chunks(sections)
        
        # Step 4: Create temporary text file with chunks
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_file:
            tmp_path = tmp_file.name
            create_chunked_text_file(chunks, tmp_path)
        
        try:
            # Step 5: Create new vector store
            vector_store_id = create_vector_store(client, "ASL Rules Vector Store v3 (Section + Page Metadata)")
            
            # Step 6: Upload processed text file
            file_id = upload_file_to_vector_store_and_wait(client, tmp_path, vector_store_id)
            
            return {
                "vector_store_id": vector_store_id,
                "file_id": file_id,
                "pdf_path": pdf_path,
                "chunking_method": "section_page_metadata",
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


def preview_extraction(pdf_path: str, num_pages: int = 3, num_chunks: int = 5):
    """Preview PDF extraction without uploading to OpenAI."""
    import pdfplumber
    
    logging.info(f"📄 Preview mode: Extracting from {pdf_path}")
    logging.info(f"   Showing first {num_pages} pages and {num_chunks} chunks...")
    logging.info("=" * 60)
    
    with pdfplumber.open(pdf_path) as pdf:
        all_text = ""
        
        for i in range(min(num_pages, len(pdf.pages))):
            page = pdf.pages[i]
            text = extract_text_two_column(page)
            all_text += text + "\n\n"
            
            logging.info(f"\n📄 PAGE {i + 1}:")
            logging.info("-" * 40)
            # Show first 500 chars of each page
            preview = text[:500] + "..." if len(text) > 500 else text
            print(preview)
            logging.info("-" * 40)
    
    # Show chunking
    chunks = simple_chunk_text(all_text, chunk_size=MAX_CHUNK_SIZE, overlap=OVERLAP)
    
    logging.info(f"\n📝 CHUNKING PREVIEW:")
    logging.info(f"   Total chunks from {num_pages} pages: {len(chunks)}")
    logging.info("=" * 60)
    
    for i, chunk in enumerate(chunks[:num_chunks]):
        logging.info(f"\n📦 CHUNK {i + 1} (length: {len(chunk)}):")
        logging.info("-" * 40)
        # Show first 300 chars of each chunk
        preview = chunk[:300] + "..." if len(chunk) > 300 else chunk
        print(preview)
        logging.info("-" * 40)


def dry_run_extraction(pdf_path: str, output_file: str = None):
    """
    Run full PDF extraction and chunking without uploading.
    Optionally saves chunks to a file for review.
    """
    import pdfplumber
    
    logging.info(f"📄 DRY RUN: Full extraction from {pdf_path}")
    logging.info("   (No upload will be performed)")
    logging.info("=" * 60)
    
    # Step 1: Extract all text
    logging.info("\n📄 Step 1: Extracting text from all pages...")
    all_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        logging.info(f"   Processing {total_pages} pages...")
        
        for i, page in enumerate(pdf.pages):
            if (i + 1) % 50 == 0:
                logging.info(f"   Processed {i + 1}/{total_pages} pages...")
            text = extract_text_two_column(page)
            if text:
                all_text += text + "\n\n"
    
    logging.info(f"✅ Extracted {len(all_text):,} characters from {total_pages} pages")
    
    # Step 2: Create chunks
    logging.info("\n📝 Step 2: Creating chunks...")
    chunks = simple_chunk_text(all_text, chunk_size=MAX_CHUNK_SIZE, overlap=OVERLAP)
    logging.info(f"✅ Created {len(chunks)} chunks")
    
    # Statistics
    chunk_sizes = [len(c) for c in chunks]
    avg_size = sum(chunk_sizes) / len(chunk_sizes) if chunks else 0
    min_size = min(chunk_sizes) if chunks else 0
    max_size = max(chunk_sizes) if chunks else 0
    
    logging.info(f"\n📊 CHUNK STATISTICS:")
    logging.info(f"   Total chunks: {len(chunks)}")
    logging.info(f"   Average size: {avg_size:.0f} chars")
    logging.info(f"   Min size: {min_size} chars")
    logging.info(f"   Max size: {max_size} chars")
    
    # Save to file if requested
    if output_file:
        logging.info(f"\n💾 Saving chunks to: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f:
            for i, chunk in enumerate(chunks):
                f.write(f"{'='*60}\n")
                f.write(f"CHUNK {i + 1} (length: {len(chunk)})\n")
                f.write(f"{'='*60}\n")
                f.write(chunk)
                f.write("\n\n")
        logging.info(f"✅ Saved {len(chunks)} chunks to {output_file}")
        logging.info(f"   Review with: less {output_file}")
    else:
        logging.info("\n💡 Tip: Use --output FILE to save chunks for review")
    
    # Show sample chunks
    logging.info(f"\n📦 SAMPLE CHUNKS (first 3):")
    for i, chunk in enumerate(chunks[:3]):
        logging.info(f"\n--- CHUNK {i + 1} ---")
        preview = chunk[:400] + "..." if len(chunk) > 400 else chunk
        print(preview)
    
    logging.info(f"\n✅ DRY RUN COMPLETE")
    logging.info(f"   To upload to OpenAI, run without --dry-run")
    
    return chunks


def main():
    """Main function to set up vector store"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Set up ASL vector store")
    parser.add_argument("--version", "-v", type=str, default="v5",
                        help="Version label for vector store (default: v5)")
    parser.add_argument("--preview", action="store_true", 
                        help="Quick preview of first few pages/chunks")
    parser.add_argument("--preview-pages", type=int, default=3,
                        help="Number of pages to preview (default: 3)")
    parser.add_argument("--preview-chunks", type=int, default=5,
                        help="Number of chunks to preview (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Full extraction without uploading to OpenAI")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output file for chunks (use with --dry-run)")
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # PDF file path - now in the evals-sft repository
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    pdf_path = project_root.parent / "mysite2-evals-sft" / "rulebook" / "eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    pdf_path = str(pdf_path.resolve())
    
    # Preview mode - quick look at first few pages
    if args.preview:
        preview_extraction(pdf_path, args.preview_pages, args.preview_chunks)
        return
    
    # Dry run mode - full extraction without upload
    if args.dry_run:
        dry_run_extraction(pdf_path, args.output)
        return
    
    logging.info("🚀 Initializing Vector Store Setup...")
    
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
        
        # Load existing config
        config_path = Path("responses_api_config.json")
        config = load_existing_config(config_path)
        
        # Set up vector store with specified version
        version = args.version
        logging.info("\n" + "="*60)
        logging.info(f"Creating {version} vector store with two-column word extraction...")
        logging.info("="*60)
        version_data = setup_asl_vector_store(client, pdf_path, version=version)
        
        # Update config with new version
        config["versions"][version] = version_data
        config["active_version"] = version
        
        # Save configuration to file
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        
        logging.info(f"\n💾 Configuration saved to {config_path}")
        logging.info(f"   Active version: {version}")
        logging.info(f"   Total versions: {len(config['versions'])}")
        
        # Test the API
        logging.info("\n🧪 Testing the setup with Responses API...")
        test_responses_api(client, version_data["vector_store_id"])
        
    except Exception as e:
        logging.error(f"Setup error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
