#!/usr/bin/env python3
"""
Script to extract text from GS Perry Sez.pdf and create an SFT dataset.
"""

import os
import json
import re
from typing import List, Dict, Tuple
from pdfminer.high_level import extract_text
from pathlib import Path
import random

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF file using pdfminer."""
    try:
        text = extract_text(pdf_path)
        return text
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return ""

def parse_qa_pairs(text: str) -> List[Tuple[str, str]]:
    """Parse Q&A pairs from the extracted text."""
    qa_pairs = []
    
    # Split text into sections by rule references
    sections = re.split(r'(?=^[A-Z]\d+\.\d+)', text, flags=re.MULTILINE)
    
    for section in sections:
        if not section.strip():
            continue
            
        lines = section.strip().split('\n')
        if len(lines) < 2:
            continue
            
        # First line should be the rule reference
        rule_ref = lines[0].strip()
        
        # Find the question and answer
        question_lines = []
        answer_lines = []
        in_answer = False
        
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
                
            # Check if this line starts an answer
            if line.startswith('A.') and not in_answer:
                in_answer = True
                answer_lines.append(line)
            elif in_answer:
                answer_lines.append(line)
            else:
                question_lines.append(line)
        
        # Only include if we have both question and answer
        if question_lines and answer_lines:
            question = ' '.join(question_lines)
            answer = ' '.join(answer_lines)
            
            # Clean up the question and answer
            question = re.sub(r'\s+', ' ', question).strip()
            answer = re.sub(r'\s+', ' ', answer).strip()
            
            # Skip if question is too short (likely just rule reference)
            if len(question) > 20:
                qa_pairs.append((question, answer))
    
    return qa_pairs

def create_sft_dataset(qa_pairs: List[Tuple[str, str]], output_file: str, max_pairs: int = 100):
    """Create SFT dataset in JSONL format."""
    
    # Take only the first max_pairs
    qa_pairs = qa_pairs[:max_pairs]
    
    with open(output_file, 'w') as f:
        for i, (question, answer) in enumerate(qa_pairs):
            # Create the SFT format entry
            sft_entry = {
                "messages": [
                    {
                        "role": "user",
                        "content": question
                    },
                    {
                        "role": "assistant",
                        "content": answer
                    }
                ]
            }
            
            # Write to JSONL file
            f.write(json.dumps(sft_entry) + '\n')
    
    print(f"Created SFT dataset with {len(qa_pairs)} Q&A pairs in {output_file}")

def split_qa_pairs(qa_pairs, train_ratio=0.8):
    random.shuffle(qa_pairs)
    split_idx = int(len(qa_pairs) * train_ratio)
    return qa_pairs[:split_idx], qa_pairs[split_idx:]

def create_eval_dataset(qa_pairs, output_file):
    with open(output_file, 'w') as f:
        for q, a in qa_pairs:
            # Try to extract section from question (e.g., "A4.34 ...")
            m = re.match(r'^([A-Z]\d+\.\d+)', q)
            section = m.group(1) if m else ""
            # Remove section from question for clarity
            question = q[len(section):].strip() if section else q
            entry = {
                "section": section,
                "question": question,
                "expected_answer": a
            }
            f.write(json.dumps(entry) + '\n')
    print(f"Created eval dataset with {len(qa_pairs)} Q&A pairs in {output_file}")

def main():
    pdf_path = "evals/sources/GS Perry Sez.pdf"
    output_dir = "evals/sources/perry_sez_split"
    os.makedirs(output_dir, exist_ok=True)
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found at {pdf_path}")
        return
    print("Extracting text from PDF...")
    text = extract_text_from_pdf(pdf_path)
    if not text:
        print("Error: Could not extract text from PDF")
        return
    print(f"Extracted {len(text)} characters of text")
    qa_pairs = parse_qa_pairs(text)
    print(f"Found {len(qa_pairs)} Q&A pairs")
    train_pairs, eval_pairs = split_qa_pairs(qa_pairs, train_ratio=0.8)
    print(f"Split: {len(train_pairs)} train, {len(eval_pairs)} eval")
    # SFT training data
    sft_file = os.path.join(output_dir, "perry_sez_train_sft.jsonl")
    create_sft_dataset(train_pairs, sft_file, max_pairs=len(train_pairs))
    # Evals data
    eval_file = os.path.join(output_dir, "perry_sez_eval.jsonl")
    create_eval_dataset(eval_pairs, eval_file)

if __name__ == "__main__":
    main() 