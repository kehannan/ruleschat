#!/usr/bin/env python3
import json
import os

def add_evaluation():
    """Add a new evaluation to the JSONL file"""
    print("=== ASL Evaluation Entry ===")
    print("(Press Ctrl+C to exit)\n")
    
    try:
        while True:
            section = input("Section (e.g., A9.74, C8): ").strip()
            if not section:
                print("Section is required!")
                continue
                
            print("\nQuestion (press Enter twice when done):")
            question_lines = []
            while True:
                line = input()
                if line == "" and question_lines:
                    break
                question_lines.append(line)
            question = "\n".join(question_lines).strip()
            
            if not question:
                print("Question is required!")
                continue
                
            print("\nExpected Answer (press Enter twice when done):")
            answer_lines = []
            while True:
                line = input()
                if line == "" and answer_lines:
                    break
                answer_lines.append(line)
            expected_answer = "\n".join(answer_lines).strip()
            
            if not expected_answer:
                print("Expected answer is required!")
                continue
            
            # Create the evaluation object
            eval_obj = {
                "section": section,
                "question": question,
                "expected_answer": expected_answer
            }
            
            # Show preview
            print("\n=== Preview ===")
            print(f"Section: {section}")
            print(f"Question: {question}")
            print(f"Expected Answer: {expected_answer}")
            
            confirm = input("\nAdd this evaluation? (y/n): ").lower().strip()
            if confirm == 'y':
                # Append to JSONL file
                with open('asl_evals_v2.jsonl', 'a', encoding='utf-8') as f:
                    f.write(json.dumps(eval_obj, ensure_ascii=False) + '\n')
                print("✓ Evaluation added!")
            else:
                print("Skipped.")
            
            print("\n" + "="*50 + "\n")
            
    except KeyboardInterrupt:
        print("\n\nGoodbye!")

if __name__ == "__main__":
    # Change to the evals directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    add_evaluation() 