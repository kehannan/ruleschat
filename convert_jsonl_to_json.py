#!/usr/bin/env python3
"""
Convert JSONL files to JSON arrays for easier viewing and formatting.
"""

import json
import sys

def jsonl_to_json_array(jsonl_file, json_file):
    """Convert JSONL file to JSON array."""
    data = []
    with open(jsonl_file, 'r') as f:
        for line in f:
            if line.strip():  # Skip empty lines
                data.append(json.loads(line))
    
    with open(json_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Converted {len(data)} entries from {jsonl_file} to {json_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python convert_jsonl_to_json.py <input.jsonl> <output.json>")
        sys.exit(1)
    
    jsonl_to_json_array(sys.argv[1], sys.argv[2]) 