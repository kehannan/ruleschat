import json
import sys
import os
import argparse

def load_results(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON in {filename}")
        sys.exit(1)

def save_results(results, filename):
    with open(filename, "w") as f:
        json.dump(results, f, indent=4)
    print(f"Results saved to {filename}")

def print_summary(results):
    auto_pass = [r for r in results if r["evaluation"] == "✅ Auto-pass"]
    human_pass = [r for r in results if r["evaluation"] == "✅ Human-pass"]
    auto_fail = [r for r in results if r["evaluation"] == "❌ Auto-fail"]
    human_fail = [r for r in results if r["evaluation"] == "❌ Human-fail"]
    needs_review = [r for r in results if r["evaluation"] == "⚠️ Needs Human Review"]
    
    print(f"\n===== EVALUATION SUMMARY =====")
    print(f"Total questions: {len(results)}")
    print(f"Auto-pass: {len(auto_pass)} ({len(auto_pass)/len(results)*100:.1f}%)")
    print(f"Human-pass: {len(human_pass)} ({len(human_pass)/len(results)*100:.1f}%)")
    print(f"Auto-fail: {len(auto_fail)} ({len(auto_fail)/len(results)*100:.1f}%)")
    print(f"Human-fail: {len(human_fail)} ({len(human_fail)/len(results)*100:.1f}%)")
    print(f"Needs review: {len(needs_review)} ({len(needs_review)/len(results)*100:.1f}%)")
    print("=============================\n")

def batch_review_by_bert_score(results, min_score, max_score, action):
    """Review all entries with BERTScore in the specified range"""
    if action not in ["pass", "fail", "skip"]:
        print("Invalid action. Must be 'pass', 'fail', or 'skip'.")
        return False
    
    if action == "skip":
        print("Skipping batch review.")
        return False
    
    count = 0
    for r in results:
        if r["evaluation"] == "⚠️ Needs Human Review" and min_score <= r["bert_f1"] <= max_score:
            if action == "pass":
                r["evaluation"] = "✅ Human-pass"
            elif action == "fail":
                r["evaluation"] = "❌ Human-fail"
            count += 1
    
    print(f"Updated {count} entries with BERTScore between {min_score} and {max_score} to '{action}'.")
    return count > 0

def batch_review_by_keyword(results, keyword, action):
    """Review all entries with a specific keyword in the question or model response"""
    if action not in ["pass", "fail", "skip"]:
        print("Invalid action. Must be 'pass', 'fail', or 'skip'.")
        return False
    
    if action == "skip":
        print("Skipping batch review.")
        return False
    
    count = 0
    for r in results:
        if r["evaluation"] == "⚠️ Needs Human Review" and (
            keyword.lower() in r["question"].lower() or 
            keyword.lower() in r["model_response"].lower()
        ):
            if action == "pass":
                r["evaluation"] = "✅ Human-pass"
            elif action == "fail":
                r["evaluation"] = "❌ Human-fail"
            count += 1
    
    print(f"Updated {count} entries containing '{keyword}' to '{action}'.")
    return count > 0

def main():
    parser = argparse.ArgumentParser(description="Batch review ASL evaluation results")
    parser.add_argument("--input", default="asl_eval_results.json", help="Input JSON file")
    parser.add_argument("--output", default="asl_eval_reviewed.json", help="Output JSON file")
    parser.add_argument("--min-bert", type=float, default=0.85, help="Minimum BERTScore for batch review")
    parser.add_argument("--max-bert", type=float, default=0.88, help="Maximum BERTScore for batch review")
    parser.add_argument("--action", choices=["pass", "fail", "skip"], default="skip", 
                        help="Action to take for entries in the BERTScore range")
    parser.add_argument("--keyword", help="Keyword to search for in questions and responses")
    parser.add_argument("--keyword-action", choices=["pass", "fail", "skip"], default="skip",
                        help="Action to take for entries containing the keyword")
    
    args = parser.parse_args()
    
    # Load results
    results = load_results(args.input)
    print_summary(results)
    
    changes_made = False
    
    # Batch review by BERTScore
    if args.action != "skip":
        changes_made = batch_review_by_bert_score(results, args.min_bert, args.max_bert, args.action) or changes_made
    
    # Batch review by keyword
    if args.keyword and args.keyword_action != "skip":
        changes_made = batch_review_by_keyword(results, args.keyword, args.keyword_action) or changes_made
    
    if changes_made:
        save_results(results, args.output)
        print_summary(results)
    else:
        print("No changes were made.")

if __name__ == "__main__":
    main() 