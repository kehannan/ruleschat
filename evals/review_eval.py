import json
import sys
import os
import textwrap

# Load the evaluation results
try:
    with open("asl_eval_results.json", "r") as f:
        results = json.load(f)
except FileNotFoundError:
    print("Error: asl_eval_results.json not found.")
    sys.exit(1)
except json.JSONDecodeError:
    print("Error: Invalid JSON in asl_eval_results.json")
    sys.exit(1)

# Count entries by evaluation status
needs_review = [r for r in results if r["evaluation"] == "⚠️ Needs Human Review"]
auto_pass = [r for r in results if r["evaluation"] == "✅ Auto-pass"]
auto_fail = [r for r in results if r["evaluation"] == "❌ Auto-fail"]

print(f"\n===== EVALUATION SUMMARY =====")
print(f"Total questions: {len(results)}")
print(f"Auto-pass: {len(auto_pass)} ({len(auto_pass)/len(results)*100:.1f}%)")
print(f"Needs review: {len(needs_review)} ({len(needs_review)/len(results)*100:.1f}%)")
print(f"Auto-fail: {len(auto_fail)} ({len(auto_fail)/len(results)*100:.1f}%)")
print("=============================\n")

# Function to review entries
def review_entries(entries):
    for i, entry in enumerate(entries):
        print(f"\nQuestion {i+1}/{len(entries)}:")
        print(textwrap.fill(f"Question: {entry['question']}", width=80))
        print(textwrap.fill(f"Expected Answer: {entry['expected_answer']}", width=80))
        print(textwrap.fill(f"Model Response: {entry['model_response']}", width=80))
        print(f"ROUGE-L: {entry['rougeL']}, BERTScore: {entry['bert_f1']}")
        decision = input("Mark as (p)ass, (f)ail, or (s)kip? ").lower()
        if decision == 'p':
            entry["evaluation"] = "✅ Auto-pass"
        elif decision == 'f':
            entry["evaluation"] = "❌ Auto-fail"
        elif decision == 's':
            continue

# Start review process
if needs_review:
    review_entries(needs_review)

# Save updated results
with open("asl_eval_reviewed.json", "w") as f:
    json.dump(results, f, indent=4)

print("Review complete. Updated results saved to asl_eval_reviewed.json") 
