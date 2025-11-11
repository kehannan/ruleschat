#!/usr/bin/env python3
"""
Generate section-to-page mapping for ASL Rulebook PDF.

This script scans the PDF and extracts section references (e.g., A4.1, C8.1)
along with their page numbers to create a mapping file.
"""

import json
import re
from pathlib import Path
import PyPDF2
from collections import defaultdict

# Section pattern: matches A4.1, C8.1, A4.15, B3.4, etc.
SECTION_PATTERN = re.compile(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b')

def is_index_or_toc_page(text, page_num):
    """
    Check if a page is likely an index or table of contents page.
    These usually have many section references but aren't the actual definitions.
    """
    # Index pages typically have:
    # - Many section references in brackets or parentheses
    # - Words like "INDEX", "TABLE OF CONTENTS"
    # - Many short lines with section references
    text_upper = text.upper()
    
    # Check for index indicators
    if 'INDEX' in text_upper or 'TABLE OF CONTENTS' in text_upper:
        return True
    
    # Early pages (first 50) with many section references are likely index
    if page_num < 50:
        # Count section references
        section_refs = len(SECTION_PATTERN.findall(text))
        # If page has many references but short text, it's likely an index
        if section_refs > 20 and len(text) < 3000:
            return True
    
    return False

def is_table_or_chart_page(text):
    """
    Check if a page is likely a table or chart (which contain references, not definitions).
    Tables/charts typically have:
    - Many dots/periods (like "...................")
    - Many section references in parentheses/brackets
    - Short lines
    - Patterns like "×1/2" or similar modifiers
    """
    # Count dots (tables often use dots for alignment)
    dot_count = text.count('.')
    # Count section references in brackets/parentheses
    ref_in_brackets = len(re.findall(r'[\[\(][A-Z]\d+\.\d+[\]\)]', text))
    # Check for table-like patterns
    has_table_patterns = bool(re.search(r'\.{3,}|×\d+|\.\.\.', text))
    
    # If page has many dots, many bracket references, and table patterns, it's likely a table
    if dot_count > 500 and ref_in_brackets > 10 and has_table_patterns:
        return True
    
    return False

def is_section_header(text, section):
    """
    Check if a section appears as a header/definition rather than just a reference.
    Headers typically:
    - Appear at start of line
    - Are followed by descriptive text
    - Are not in brackets or parentheses
    """
    lines = text.split('\n')
    for line in lines:
        stripped = line.strip()
        # Check if section appears at start of line or after minimal whitespace
        if re.match(rf'^\s*{re.escape(section)}\b', stripped):
            # Make sure it's not just in brackets/parentheses (which indicates a reference)
            if not re.search(rf'[\[\(]{re.escape(section)}[\]\)]', text):
                # Check if there's substantial text after it (suggests it's a definition)
                after_section = stripped[len(section):].strip()
                if len(after_section) > 10:  # Has meaningful content after
                    return True
    return False

def extract_sections_from_pdf(pdf_path):
    """
    Extract section references and their page numbers from PDF.
    
    Returns a dict mapping section -> list of page numbers where it appears,
    with priority given to pages where it appears as a header.
    """
    section_pages = defaultdict(list)
    section_header_pages = {}  # Pages where section appears as header
    
    print(f"Scanning PDF: {pdf_path}")
    
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        total_pages = len(reader.pages)
        print(f"Total pages: {total_pages}\n")
        
        for page_num in range(total_pages):
            if (page_num + 1) % 50 == 0:
                print(f"Processing page {page_num + 1}/{total_pages}...", end='\r')
            
            try:
                page = reader.pages[page_num]
                text = page.extract_text()
                
                # Skip index/TOC pages
                if is_index_or_toc_page(text, page_num + 1):
                    continue
                
                # Skip table/chart pages (they contain references, not definitions)
                is_table = is_table_or_chart_page(text)
                
                # Find all section references in this page
                matches = SECTION_PATTERN.findall(text)
                
                for section in set(matches):  # Use set to avoid duplicates on same page
                    # Check if this section appears as a header on this page
                    if is_section_header(text, section):
                        section_header_pages[section] = page_num + 1
                    elif not is_table:
                        # Regular occurrence (but not in a table)
                        if (page_num + 1) not in section_pages[section]:
                            section_pages[section].append(page_num + 1)
                        
            except Exception as e:
                print(f"\nError processing page {page_num + 1}: {e}")
                continue
    
    print(f"\n\nFound {len(section_pages)} unique sections")
    print(f"Found {len(section_header_pages)} sections with header occurrences")
    return section_pages, section_header_pages

def select_best_page(section_pages, section_header_pages):
    """
    For each section, select the best page number.
    
    Strategy:
    - If section appears as a header, use that page (highest priority)
    - Otherwise, use the first occurrence (earliest page)
    """
    mapping = {}
    
    # First, add all sections that appear as headers
    for section, page in section_header_pages.items():
        mapping[section] = page
    
    # Then, add sections that don't have headers, using first occurrence
    for section, pages in section_pages.items():
        if section not in mapping:  # Don't override header pages
            pages_sorted = sorted(set(pages))
            if pages_sorted:
                mapping[section] = pages_sorted[0]
    
    return mapping

def main():
    # Paths
    project_root = Path(__file__).parent.parent
    pdf_path = project_root / "static" / "rulebook" / "eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    output_path = project_root / "static" / "rulebook" / "section_pages.json"
    
    if not pdf_path.exists():
        print(f"Error: PDF not found at {pdf_path}")
        return
    
    # Extract sections
    section_pages, section_header_pages = extract_sections_from_pdf(pdf_path)
    
    # Create mapping (section -> page number)
    mapping = select_best_page(section_pages, section_header_pages)
    
    # Sort by section for readability
    sorted_mapping = dict(sorted(mapping.items(), key=lambda x: (
        x[0][0],  # Sort by letter (A, B, C, etc.)
        [int(n) for n in x[0][1:].split('.')]  # Then by numbers
    )))
    
    # Save to JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(sorted_mapping, f, indent=2)
    
    print(f"\n✅ Mapping saved to: {output_path}")
    print(f"   Total sections mapped: {len(sorted_mapping)}")
    
    # Show some examples
    print("\nSample mappings:")
    for i, (section, page) in enumerate(list(sorted_mapping.items())[:10]):
        print(f"  {section} → Page {page}")
    
    # Show statistics
    print(f"\nStatistics:")
    print(f"  Sections starting with A: {sum(1 for s in sorted_mapping if s.startswith('A'))}")
    print(f"  Sections starting with B: {sum(1 for s in sorted_mapping if s.startswith('B'))}")
    print(f"  Sections starting with C: {sum(1 for s in sorted_mapping if s.startswith('C'))}")
    print(f"  Sections starting with D: {sum(1 for s in sorted_mapping if s.startswith('D'))}")

if __name__ == "__main__":
    main()
