#!/usr/bin/env python3
"""
View link objects in a PDF page.

Usage:
    python3 scripts/view_pdf_links.py [page_number]
    
Example:
    python3 scripts/view_pdf_links.py 12
"""

import sys
import PyPDF2
from pathlib import Path

def resolve_page_ref(reader, page_ref):
    """Resolve an IndirectObject page reference to a page number."""
    try:
        if hasattr(page_ref, 'get_object'):
            for j, p in enumerate(reader.pages):
                try:
                    if p.get_object().indirect_reference == page_ref:
                        return j + 1
                except:
                    pass
    except:
        pass
    return None

def convert_pdf_value(val):
    """Convert PyPDF2 objects to Python native types."""
    if hasattr(val, '__float__'):
        try:
            return float(val)
        except:
            return str(val)
    elif hasattr(val, '__int__'):
        try:
            return int(val)
        except:
            return str(val)
    elif isinstance(val, (list, tuple)):
        return [convert_pdf_value(x) for x in val]
    else:
        return str(val)

def main():
    pdf_path = Path(__file__).parent.parent / "static" / "rulebook" / "eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    page_num = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        page = reader.pages[page_num - 1]  # Convert to 0-indexed
        text = page.extract_text()
        
        print(f"=== Link Objects on Page {page_num} ===\n")
        
        if '/Annots' not in page:
            print("No annotations found on this page.")
            return
        
        annots = page['/Annots']
        links = []
        
        for i, annot_ref in enumerate(annots):
            annot = annot_ref.get_object()
            if annot.get('/Subtype') == '/Link':
                rect = annot.get('/Rect')
                dest = annot.get('/Dest')
                
                link_info = {
                    'annotation_index': i,
                    'rect': convert_pdf_value(rect) if rect else None,
                }
                
                if link_info['rect'] and len(link_info['rect']) >= 4:
                    link_info['y_pos'] = link_info['rect'][1]  # Bottom Y
                
                if dest and isinstance(dest, list) and len(dest) > 0:
                    page_ref = dest[0]
                    dest_page = resolve_page_ref(reader, page_ref)
                    link_info['dest_page'] = dest_page
                    link_info['view_type'] = str(dest[1]) if len(dest) > 1 else None
                
                links.append(link_info)
        
        # Sort by Y position
        links_sorted = sorted(links, key=lambda x: -(x.get('y_pos') or 0))
        
        print(f"Total links: {len(links)}\n")
        print("Links sorted by Y position (top to bottom):")
        print("-" * 80)
        print(f"{'Idx':<5} {'Ann#':<6} {'Y-pos':<10} {'Dest':<8} {'Rectangle'}")
        print("-" * 80)
        
        for idx, link in enumerate(links_sorted):
            y_pos = link.get('y_pos', 0) or 0
            dest = link.get('dest_page', 'N/A')
            rect = link.get('rect', [])
            rect_str = f"[{rect[0]:.1f}, {rect[1]:.1f}, {rect[2]:.1f}, {rect[3]:.1f}]" if rect and len(rect) >= 4 else "N/A"
            
            marker = ""
            if dest == 48:
                marker = " ← page 48"
            elif dest == 57:
                marker = " ← page 57 (A7.36 content)"
            
            print(f"{idx:<5} {link['annotation_index']:<6} {y_pos:<10.1f} {dest:<8} {rect_str}{marker}")
        
        # Show specific links of interest
        print("\n" + "=" * 80)
        print("Links of interest:")
        print("=" * 80)
        
        link_48 = next((l for l in links if l.get('dest_page') == 48), None)
        if link_48:
            idx = links_sorted.index(link_48)
            print(f"\nLink to page 48:")
            print(f"  Annotation index: {link_48['annotation_index']}")
            print(f"  Sorted position: {idx}")
            print(f"  Rectangle: {link_48['rect']}")
        
        link_57 = next((l for l in links if l.get('dest_page') == 57), None)
        if link_57:
            idx = links_sorted.index(link_57)
            print(f"\nLink to page 57 (A7.36 content):")
            print(f"  Annotation index: {link_57['annotation_index']}")
            print(f"  Sorted position: {idx}")
            print(f"  Rectangle: {link_57['rect']}")
        
        # Find A7.36 in text
        a7_36_pos = text.find('A7.36')
        if a7_36_pos >= 0:
            print(f"\nA7.36 text position: {a7_36_pos}")
            print(f"Context: {text[max(0, a7_36_pos-50):a7_36_pos+100]}")

if __name__ == "__main__":
    main()

