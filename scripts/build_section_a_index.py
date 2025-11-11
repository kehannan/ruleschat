#!/usr/bin/env python3
"""
Build an index of Section A rules from the ASL Rulebook PDF.

This script extracts:
- Section Letter: A
- Section: A3 or A3.1
- Section title: BASIC SEQUENCE OF PLAY
- Page: 48

And saves to a JSON file for use in building the vector store.
"""

import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pdfplumber
from typing import List, Dict, Tuple

# Pattern to match section headers like "A 3.1" or "A3.1"
SECTION_HEADER_PATTERN = re.compile(r'^A\s*(\d+(?:\.\d+)?)\s+(.+)$', re.MULTILINE)

# Keywords that suggest a section header
SECTION_KEYWORDS = [
    'PHASE', 'SEQUENCE', 'RULES', 'FIRE', 'MOVE', 'ATTACK', 'DEFENSE',
    'COMBAT', 'TERRAIN', 'VEHICLE', 'INFANTRY', 'LEADER', 'ORDNANCE',
    'MORALE', 'ROUT', 'BROKEN', 'CONCEALMENT', 'ENTRY', 'EXIT', 'SETUP'
]


def extract_section_letter_from_page(page_text: str, page_num: int) -> str:
    """
    Extract the section letter from page headers.
    Page headers typically have a large letter (A, B, C, etc.) indicating the section.
    """
    # Look for single capital letters at the start of lines
    # Often page headers have the section letter prominently displayed
    lines = page_text.split('\n')
    
    # Check first few lines for a standalone capital letter
    for line in lines[:5]:
        stripped = line.strip()
        # Look for single letter or letter with number (like "A" or "A3")
        match = re.match(r'^([A-Z])(?:\s+\d+)?$', stripped)
        if match:
            return match.group(1)
    
    # Fallback: look for patterns like "SECTION A" or "A." at start
    for line in lines[:10]:
        if re.search(r'\bSECTION\s+([A-Z])\b', line, re.IGNORECASE):
            return re.search(r'\bSECTION\s+([A-Z])\b', line, re.IGNORECASE).group(1).upper()
        if re.match(r'^([A-Z])\.?\s*$', line.strip()):
            return line.strip()[0]
    
    return None


def is_section_header(line: str, next_line: str = None) -> Tuple[bool, str]:
    """
    Determine if a line is a section header and extract the title.
    
    Returns: (is_header, title)
    """
    stripped = line.strip()
    
    # Must start with "A" followed by number
    match = re.match(r'^A\s*(\d+(?:\.\d+)?)\s+(.+)$', stripped)
    if not match:
        return False, ""
    
    section_num = match.group(1)
    rest = match.group(2).strip()
    
    if not rest:
        return False, ""
    
    # Check various patterns for section headers
    
    # Pattern 1: Title in ALL CAPS (common for section headers)
    if rest.isupper() and len(rest) > 3:
        # Extract title (up to colon or reasonable length)
        title = re.split(r'[:;]', rest)[0].strip()
        if len(title) > 3:
            return True, title
    
    # Pattern 2: Title starts with caps and has keywords
    if rest[0].isupper():
        # Check for section keywords
        rest_upper = rest.upper()
        has_keyword = any(keyword in rest_upper for keyword in SECTION_KEYWORDS)
        
        if has_keyword:
            # Extract title (up to colon, semicolon, or parentheses with page refs)
            title = re.split(r'[:;]|\([A-Z]\d+', rest)[0].strip()
            if len(title) > 3:
                return True, title
    
    # Pattern 3: Title in mixed case but followed by colon (common pattern)
    if ':' in rest:
        title = rest.split(':')[0].strip()
        # Check if title looks reasonable (has some caps, reasonable length)
        if any(c.isupper() for c in title) and 3 < len(title) < 80:
            return True, title
    
    # Pattern 4: Check if next line is content (not another section)
    # This helps distinguish headers from inline references
    if next_line:
        next_stripped = next_line.strip()
        # If next line starts with section reference, this is probably not a header
        if re.match(r'^[A-Z]\s*\d+', next_stripped):
            return False, ""
        # If next line is content (lowercase start or long), this might be a header
        if next_stripped and (next_stripped[0].islower() or len(next_stripped) > 50):
            # Extract reasonable title from rest
            title = re.split(r'[:;]|\([A-Z]\d+', rest)[0].strip()[:60]
            if len(title) > 3:
                return True, title
    
    return False, ""


def build_section_a_index(pdf_path: str) -> List[Dict]:
    """
    Build index of Section A rules from PDF.
    
    Returns list of dicts with: section_letter, section, section_title, page
    """
    print(f"Building Section A index from: {pdf_path}")
    print("=" * 80)
    
    index = []
    seen_sections = set()  # Track sections we've already found
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        print(f"Processing {total_pages} pages...\n")
        
        for page_num in range(total_pages):
            if (page_num + 1) % 50 == 0:
                print(f"  Processed {page_num + 1}/{total_pages} pages...", end='\r')
            
            page = pdf.pages[page_num]
            text = page.extract_text()
            
            if not text:
                continue
            
            # Look for section headers in this page
            # Handle cases where "A" and the number might be on separate lines
            lines = text.split('\n')
            
            # First, try to find section headers that might be split across lines
            # Look for "A" on one line, followed by number on next line
            for i in range(len(lines) - 1):
                line1 = lines[i].strip()
                line2 = lines[i + 1].strip() if i + 1 < len(lines) else ""
                
                # Pattern 1: "A" on line 1, number on line 2
                if line1 == "A" and re.match(r'^\d+(?:\.\d+)?', line2):
                    section_match = re.match(r'^(\d+(?:\.\d+)?)\s*(.+)$', line2)
                    if section_match:
                        section_num = section_match.group(1)
                        rest = section_match.group(2).strip()
                        
                        # Try to extract title from rest or next lines
                        title = ""
                        if rest:
                            # Check if rest starts with caps (likely title)
                            title_match = re.match(r'^([A-Z][A-Z\s\(\):]+?)(?::|$)', rest)
                            if title_match:
                                title = title_match.group(1).strip()
                        
                        # If no title yet, check next line
                        if not title and i + 2 < len(lines):
                            next_line = lines[i + 2].strip()
                            if next_line and next_line[0].isupper():
                                # Extract title from next line
                                title_match = re.match(r'^([A-Z][A-Z\s\(\):]+?)(?::|$)', next_line)
                                if title_match:
                                    title = title_match.group(1).strip()
                        
                        # Filter out obvious false positives
                        # Skip if title looks like a table entry (multiple section refs)
                        if re.search(r'[A-Z]\d+\s+[A-Z]\d+', title):
                            continue
                        # Skip if title starts with special chars (likely inline ref)
                        if title.startswith(('&', '[', '(', ')')):
                            continue
                        # Skip if title is lowercase (likely content, not header)
                        if title and title[0].islower():
                            continue
                        
                        if title and len(title) > 3:
                            section = f'A{section_num}'
                            if section not in seen_sections:
                                seen_sections.add(section)
                                index.append({
                                    'section_letter': 'A',
                                    'section': section,
                                    'section_title': title,
                                    'page': page_num + 1
                                })
            
            # Also check for section headers on single lines
            for i, line in enumerate(lines):
                # Check if this line is a section header
                next_line = lines[i + 1] if i + 1 < len(lines) else None
                is_header, title = is_section_header(line, next_line)
                
                if is_header:
                    # Extract section number
                    match = re.match(r'^A\s*(\d+(?:\.\d+)?)', line.strip())
                    if match:
                        section_num = match.group(1)
                        section = f'A{section_num}'
                        
                        # Filter out obvious false positives
                        # Skip if title looks like a table entry (multiple section refs)
                        if re.search(r'[A-Z]\d+\s+[A-Z]\d+', title):
                            continue
                        # Skip if title starts with special chars (likely inline ref)
                        if title.startswith(('&', '[', '(', ')')):
                            continue
                        # Skip if title is lowercase (likely content, not header)
                        if title and title[0].islower():
                            continue
                        
                        # Only add if we haven't seen this section before
                        # (use first occurrence as the canonical one)
                        if section not in seen_sections:
                            seen_sections.add(section)
                            index.append({
                                'section_letter': 'A',
                                'section': section,
                                'section_title': title,
                                'page': page_num + 1
                            })
        
        print(f"\n\n✅ Found {len(index)} Section A rules")
    
    return index


def main():
    """Main function"""
    # PDF path
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    pdf_path = project_root.parent / "mysite2-evals-sft" / "rulebook" / "eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    pdf_path = str(pdf_path.resolve())
    
    if not Path(pdf_path).exists():
        print(f"❌ PDF not found: {pdf_path}")
        sys.exit(1)
    
    # Build index
    index = build_section_a_index(pdf_path)
    
    # Sort by section number (A3, A3.1, A4, etc.)
    def section_sort_key(item):
        section = item['section']
        # Extract numeric parts for sorting
        parts = section[1:].split('.')
        return tuple(int(p) for p in parts)
    
    index.sort(key=section_sort_key)
    
    # Save to JSON
    output_file = project_root / "section_a_index.json"
    with open(output_file, 'w') as f:
        json.dump(index, f, indent=2)
    
    print(f"\n💾 Index saved to: {output_file}")
    print(f"\n📊 Summary:")
    print(f"   Total sections: {len(index)}")
    print(f"   Page range: {min(s['page'] for s in index)} - {max(s['page'] for s in index)}")
    
    # Show sample
    print(f"\n📋 Sample entries (first 10):")
    print("=" * 80)
    for i, entry in enumerate(index[:10], 1):
        print(f"{i:2d}. Page {entry['page']:3d} | {entry['section']:8s} | {entry['section_title'][:50]}")


if __name__ == "__main__":
    main()

