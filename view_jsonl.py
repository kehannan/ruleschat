#!/usr/bin/env python3
"""
Simple JSONL viewer - displays JSONL files in a readable format.
"""

import json
import sys

def view_jsonl(filename, max_lines=None):
    """View JSONL file in a readable format."""
    with open(filename, 'r') as f:
        for i, line in enumerate(f, 1):
            if max_lines and i > max_lines:
                print(f"\n... (showing first {max_lines} entries)")
                break
            if line.strip():
                data = json.loads(line)
                print(f"\n--- Entry {i} ---")
                print(f"Section: {data.get('section', 'N/A')}")
                print(f"Question: {data.get('question', 'N/A')}")
                print(f"Answer: {data.get('expected_answer', 'N/A')}")
                print("-" * 50)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python view_jsonl.py <file.jsonl> [max_lines]")
        sys.exit(1)
    
    filename = sys.argv[1]
    max_lines = int(sys.argv[2]) if len(sys.argv) > 2 else None
    
    view_jsonl(filename, max_lines) 