#!/usr/bin/env python3
import csv
import json
import os

def csv_to_jsonl(csv_file='asl_evals.csv', jsonl_file='asl_evals_v2.jsonl'):
    """Convert CSV file to JSONL format"""
    
    if not os.path.exists(csv_file):
        # Create a sample CSV file
        print(f"Creating sample CSV file: {csv_file}")
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['section', 'question', 'expected_answer'])
            writer.writerow(['A9.74', 'Sample question here', 'Sample answer here'])
        print(f"Sample CSV created! Edit {csv_file} and run this script again.")
        return
    
    # Read CSV and convert to JSONL
    evaluations = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['section'].strip() and row['question'].strip() and row['expected_answer'].strip():
                evaluations.append({
                    'section': row['section'].strip(),
                    'question': row['question'].strip(),
                    'expected_answer': row['expected_answer'].strip()
                })
    
    # Write to JSONL (append mode)
    with open(jsonl_file, 'a', encoding='utf-8') as f:
        for eval_obj in evaluations:
            f.write(json.dumps(eval_obj, ensure_ascii=False) + '\n')
    
    print(f"Added {len(evaluations)} evaluations to {jsonl_file}")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    csv_to_jsonl() 