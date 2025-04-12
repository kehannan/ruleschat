import json
import sys
import os
import argparse
from datetime import datetime

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

def calculate_metrics(results):
    """Calculate overall metrics from the evaluation results"""
    metrics = {
        "total_questions": len(results),
        "auto_pass": len([r for r in results if r["evaluation"] == "✅ Auto-pass"]),
        "human_pass": len([r for r in results if r["evaluation"] == "✅ Human-pass"]),
        "auto_fail": len([r for r in results if r["evaluation"] == "❌ Auto-fail"]),
        "human_fail": len([r for r in results if r["evaluation"] == "❌ Human-fail"]),
        "needs_review": len([r for r in results if r["evaluation"] == "⚠️ Needs Human Review"]),
        "avg_rougeL": sum(r["rougeL"] for r in results) / len(results),
        "avg_bert_f1": sum(r["bert_f1"] for r in results) / len(results),
        "min_rougeL": min(r["rougeL"] for r in results),
        "max_rougeL": max(r["rougeL"] for r in results),
        "min_bert_f1": min(r["bert_f1"] for r in results),
        "max_bert_f1": max(r["bert_f1"] for r in results),
    }
    
    # Calculate pass rate
    total_evaluated = metrics["total_questions"] - metrics["needs_review"]
    if total_evaluated > 0:
        metrics["pass_rate"] = (metrics["auto_pass"] + metrics["human_pass"]) / total_evaluated * 100
    else:
        metrics["pass_rate"] = 0
    
    return metrics

def generate_html_report(results, metrics, output_file):
    """Generate an HTML report with the evaluation results"""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ASL Evaluation Report</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            color: #333;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1, h2, h3 {{
            color: #2c3e50;
        }}
        .summary {{
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 30px;
        }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .metric-card {{
            background-color: #fff;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: bold;
            color: #3498db;
        }}
        .questions {{
            margin-top: 30px;
        }}
        .question {{
            background-color: #fff;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .question h3 {{
            margin-top: 0;
        }}
        .status {{
            display: inline-block;
            padding: 5px 10px;
            border-radius: 3px;
            font-weight: bold;
        }}
        .status-pass {{
            background-color: #d4edda;
            color: #155724;
        }}
        .status-fail {{
            background-color: #f8d7da;
            color: #721c24;
        }}
        .status-review {{
            background-color: #fff3cd;
            color: #856404;
        }}
        .scores {{
            margin-top: 10px;
            font-size: 14px;
            color: #6c757d;
        }}
        .expected, .model-response {{
            margin-top: 15px;
        }}
        .label {{
            font-weight: bold;
            margin-bottom: 5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>ASL Evaluation Report</h1>
        <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        
        <div class="summary">
            <h2>Summary</h2>
            <div class="metrics">
                <div class="metric-card">
                    <div>Total Questions</div>
                    <div class="metric-value">{metrics['total_questions']}</div>
                </div>
                <div class="metric-card">
                    <div>Pass Rate</div>
                    <div class="metric-value">{metrics['pass_rate']:.1f}%</div>
                </div>
                <div class="metric-card">
                    <div>Auto Pass</div>
                    <div class="metric-value">{metrics['auto_pass']} ({metrics['auto_pass']/metrics['total_questions']*100:.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div>Human Pass</div>
                    <div class="metric-value">{metrics['human_pass']} ({metrics['human_pass']/metrics['total_questions']*100:.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div>Auto Fail</div>
                    <div class="metric-value">{metrics['auto_fail']} ({metrics['auto_fail']/metrics['total_questions']*100:.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div>Human Fail</div>
                    <div class="metric-value">{metrics['human_fail']} ({metrics['human_fail']/metrics['total_questions']*100:.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div>Needs Review</div>
                    <div class="metric-value">{metrics['needs_review']} ({metrics['needs_review']/metrics['total_questions']*100:.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div>Avg RougeL</div>
                    <div class="metric-value">{metrics['avg_rougeL']:.3f}</div>
                </div>
                <div class="metric-card">
                    <div>Avg BERT F1</div>
                    <div class="metric-value">{metrics['avg_bert_f1']:.3f}</div>
                </div>
            </div>
        </div>
        
        <div class="questions">
            <h2>Questions</h2>
"""
    
    # Sort results by evaluation status and then by BERT score
    sorted_results = sorted(
        results, 
        key=lambda r: (
            "1" if "Auto-pass" in r["evaluation"] else 
            "2" if "Human-pass" in r["evaluation"] else 
            "3" if "Needs Human Review" in r["evaluation"] else 
            "4" if "Auto-fail" in r["evaluation"] else "5",
            -r["bert_f1"]
        )
    )
    
    for r in sorted_results:
        status_class = ""
        if "pass" in r["evaluation"].lower():
            status_class = "status-pass"
        elif "fail" in r["evaluation"].lower():
            status_class = "status-fail"
        else:
            status_class = "status-review"
        
        html += f"""
            <div class="question">
                <h3>{r["question"]}</h3>
                <div class="status {status_class}">{r["evaluation"]}</div>
                <div class="scores">
                    RougeL: {r["rougeL"]:.3f} | BERT F1: {r["bert_f1"]:.3f}
                </div>
                <div class="expected">
                    <div class="label">Expected Answer:</div>
                    <p>{r["expected_answer"]}</p>
                </div>
                <div class="model-response">
                    <div class="label">Model Response:</div>
                    <p>{r["model_response"]}</p>
                </div>
            </div>
"""
    
    html += """
        </div>
    </div>
</body>
</html>
"""
    
    with open(output_file, "w") as f:
        f.write(html)
    
    print(f"HTML report generated: {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Generate a report from ASL evaluation results")
    parser.add_argument("--input", default="asl_eval_reviewed.json", help="Input JSON file with reviewed results")
    parser.add_argument("--output", default="asl_eval_report.html", help="Output HTML report file")
    
    args = parser.parse_args()
    
    # Load results
    results = load_results(args.input)
    
    # Calculate metrics
    metrics = calculate_metrics(results)
    
    # Generate HTML report
    generate_html_report(results, metrics, args.output)
    
    # Print summary to console
    print("\n===== EVALUATION SUMMARY =====")
    print(f"Total questions: {metrics['total_questions']}")
    print(f"Pass rate: {metrics['pass_rate']:.1f}%")
    print(f"Auto-pass: {metrics['auto_pass']} ({metrics['auto_pass']/metrics['total_questions']*100:.1f}%)")
    print(f"Human-pass: {metrics['human_pass']} ({metrics['human_pass']/metrics['total_questions']*100:.1f}%)")
    print(f"Auto-fail: {metrics['auto_fail']} ({metrics['auto_fail']/metrics['total_questions']*100:.1f}%)")
    print(f"Human-fail: {metrics['human_fail']} ({metrics['human_fail']/metrics['total_questions']*100:.1f}%)")
    print(f"Needs review: {metrics['needs_review']} ({metrics['needs_review']/metrics['total_questions']*100:.1f}%)")
    print(f"Average RougeL: {metrics['avg_rougeL']:.3f}")
    print(f"Average BERT F1: {metrics['avg_bert_f1']:.3f}")
    print("=============================\n")

if __name__ == "__main__":
    main() 