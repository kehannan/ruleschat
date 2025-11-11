#!/usr/bin/env python3
"""
Generate section-to-page mapping from PDF index/table of contents.

This script extracts section references from the PDF's index pages and uses
the internal link destinations to map sections to their correct pages.
"""

import json
import re
from pathlib import Path
import PyPDF2

# Section pattern: matches A4.1, C8.1, A4.15, B3.4, etc.
SECTION_PATTERN = re.compile(r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b')

def find_index_pages(reader):
    """
    Find pages that are likely the index/table of contents.
    Usually early pages with many links and section references.
    """
    index_pages = []
    
    # Check first 50 pages
    for page_num in range(min(50, len(reader.pages))):
        try:
            page = reader.pages[page_num]
            text = page.extract_text()
            
            # Index pages typically have:
            # - Many link annotations
            # - Many section references
            # - Words like "TABLE OF CONTENTS" or "INDEX"
            text_upper = text.upper()
            has_index_keywords = 'TABLE OF CONTENTS' in text_upper or 'INDEX' in text_upper
            
            # Count links and section references
            link_count = 0
            if '/Annots' in page:
                annots = page['/Annots']
                for annot_ref in annots:
                    annot = annot_ref.get_object()
                    if annot.get('/Subtype') == '/Link':
                        link_count += 1
            
            section_refs = len(SECTION_PATTERN.findall(text))
            
            # If it has index keywords OR has many links with section refs, it's likely an index page
            if has_index_keywords or (link_count > 10 and section_refs > 5):
                index_pages.append(page_num)
        except:
            pass
    
    return index_pages

def extract_text_from_link_area(page, rect):
    """
    Try to extract text from the area covered by a link.
    This is approximate since we can't perfectly map coordinates to text.
    """
    try:
        text = page.extract_text()
        # This is a simplified approach - in reality, we'd need to map
        # PDF coordinates to text positions, which is complex
        # For now, we'll extract the link text differently
        return None
    except:
        return None

def extract_section_from_link_text(page, link_rect):
    """
    Extract the section reference text near a link.
    We'll search the page text and find section patterns near the link coordinates.
    """
    try:
        text = page.extract_text()
        lines = text.split('\n')
        
        # The link rect is [left, bottom, right, top] in PDF coordinates
        # We can't perfectly map this, but we can search for section patterns
        # and assume links are near section references in the text
        
        # Find all section references on the page
        matches = list(SECTION_PATTERN.finditer(text))
        
        # For now, return all sections on the page - we'll match them with links
        return [m.group(1) for m in matches]
    except:
        return []

def get_link_destination_page(reader, link_annot):
    """
    Get the page number that a link points to.
    """
    try:
        # Check for direct /Dest
        dest = link_annot.get('/Dest')
        if dest:
            if isinstance(dest, list) and len(dest) > 0:
                page_ref = dest[0]
                # Resolve the page reference
                if hasattr(page_ref, 'get_object'):
                    page_obj = page_ref.get_object()
                    # Find which page this object refers to
                    for i, page in enumerate(reader.pages):
                        try:
                            if page.get_object().indirect_reference == page_ref:
                                return i + 1
                        except:
                            pass
                else:
                    # Might be a direct page number (unlikely but possible)
                    try:
                        return int(page_ref) + 1
                    except:
                        pass
        
        # Check for /A (Action) with /GoTo
        action = link_annot.get('/A')
        if action:
            if hasattr(action, 'get_object'):
                action_obj = action.get_object()
            else:
                action_obj = action
            
            if action_obj.get('/S') == '/GoTo':
                dest = action_obj.get('/D')
                if dest and isinstance(dest, list) and len(dest) > 0:
                    page_ref = dest[0]
                    # Resolve the page reference
                    if hasattr(page_ref, 'get_object'):
                        page_obj = page_ref.get_object()
                        for i, page in enumerate(reader.pages):
                            try:
                                if page.get_object().indirect_reference == page_ref:
                                    return i + 1
                            except:
                                pass
    except Exception as e:
        pass
    
    return None

def extract_sections_from_index(reader, index_pages):
    """
    Extract section-to-page mappings from index pages using link annotations.
    
    Strategy:
    1. For each link, get its destination page
    2. Find section references in the text near where the link appears
    3. Match links to sections based on text order and link order
    """
    section_to_page = {}
    
    print(f"Processing {len(index_pages)} index pages...")
    
    for page_num in index_pages:
        try:
            page = reader.pages[page_num]
            text = page.extract_text()
            
            if '/Annots' not in page:
                continue
            
            annots = page['/Annots']
            
            # Extract all section references with their positions in the text
            section_matches = list(SECTION_PATTERN.finditer(text))
            section_data = [(m.start(), m.end(), m.group(1)) for m in section_matches]
            
            # Get all links with their destinations
            links_with_dests = []
            for annot_ref in annots:
                annot = annot_ref.get_object()
                if annot.get('/Subtype') != '/Link':
                    continue
                
                dest_page = get_link_destination_page(reader, annot)
                if dest_page:
                    rect = annot.get('/Rect', [])
                    links_with_dests.append({
                        'dest_page': dest_page,
                        'rect': rect,
                        'annot': annot
                    })
            
            # Match links to sections
            # Strategy: Sections and links appear in roughly the same order.
            # Match section at index N to link at position N (or closest position) in their respective lists.
            
            if not section_data or not links_with_dests:
                continue
            
            # Sort sections by their position in text (order of appearance)
            section_data_sorted = sorted(section_data, key=lambda x: x[0])
            
            # Sort links by their annotation order (they're already in order from the annotations array)
            # But we need to map annotation indices to positions in links_with_dests
            # links_with_dests is already in annotation order, so we can use index directly
            
            # Match sections to links
            # Strategy: For each section, find the link whose destination page actually contains that section
            # This is the most reliable method - verify the destination has the section
            num_sections = len(section_data_sorted)
            num_links = len(links_with_dests)
            
            for i, (start_pos, end_pos, section) in enumerate(section_data_sorted):
                if section in section_to_page:
                    continue  # Already mapped (first occurrence across all pages wins)
                
                # First, try to find a link whose destination page contains this section as a definition
                best_match = None
                best_match_page = None
                
                # Check links near this section's position (within ±10 positions)
                search_range = 10
                start_idx = max(0, i - search_range)
                end_idx = min(num_links, i + search_range + 1)
                
                for link_idx in range(start_idx, end_idx):
                    link = links_with_dests[link_idx]
                    dest_page = link['dest_page']
                    
                    # Check if destination page contains this section
                    try:
                        dest_page_obj = reader.pages[dest_page - 1]
                        dest_text = dest_page_obj.extract_text()
                        
                        if section in dest_text:
                            # Check if it looks like a definition (at start of line)
                            lines = dest_text.split('\n')
                            for line in lines:
                                stripped = line.strip()
                                if stripped.startswith(section) or re.match(rf'^\s*{re.escape(section)}\b', stripped):
                                    # Found a definition!
                                    best_match = link
                                    best_match_page = dest_page
                                    break
                    except:
                        pass
                    
                    if best_match:
                        break
                
                # If we found a good match, use it
                if best_match_page:
                    section_to_page[section] = best_match_page
                elif i < num_links:
                    # Fallback: use link at same position
                    link = links_with_dests[i]
                    section_to_page[section] = link['dest_page']
                elif num_links > 0:
                    # Final fallback: use last link
                    section_to_page[section] = links_with_dests[-1]['dest_page']
                    
        except Exception as e:
            print(f"Error processing index page {page_num + 1}: {e}")
            continue
    
    return section_to_page

def main():
    # Paths
    project_root = Path(__file__).parent.parent
    pdf_path = project_root / "static" / "rulebook" / "eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    output_path = project_root / "static" / "rulebook" / "section_pages.json"
    
    if not pdf_path.exists():
        print(f"Error: PDF not found at {pdf_path}")
        return
    
    print(f"Reading PDF: {pdf_path}")
    
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        total_pages = len(reader.pages)
        print(f"Total pages: {total_pages}\n")
        
        # Find index pages
        index_pages = find_index_pages(reader)
        print(f"Found {len(index_pages)} index pages: {[p+1 for p in index_pages]}\n")
        
        # Extract section mappings from index
        section_to_page = extract_sections_from_index(reader, index_pages)
        
        print(f"\nExtracted {len(section_to_page)} section mappings from index")
    
    # Sort by section for readability
    sorted_mapping = dict(sorted(section_to_page.items(), key=lambda x: (
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
    for i, (section, page) in enumerate(list(sorted_mapping.items())[:15]):
        print(f"  {section} → Page {page}")
    
    # Test A7.36 specifically
    if 'A7.36' in sorted_mapping:
        print(f"\n✅ A7.36 → Page {sorted_mapping['A7.36']}")
    else:
        print(f"\n❌ A7.36 not found in mapping")

if __name__ == "__main__":
    main()

