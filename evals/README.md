# ASL Evaluation Tools

This directory contains tools for evaluating model responses to Advanced Squad Leader (ASL) questions.

## Files

- `asl_eval.py` - The main evaluation script that compares model responses to expected answers
- `asl_eval.jsonl` - The dataset of questions and expected answers
- `asl_eval_results.json` - Results of the evaluation with metrics and evaluation status
- `review_eval.py` - Script for manually reviewing entries that need human judgment
- `batch_review.py` - Script for batch reviewing entries based on criteria
- `generate_report.py` - Script for generating an HTML report of evaluation results

## Usage

### 1. Run the Evaluation

The evaluation has already been run, and the results are in `asl_eval_results.json`. If you need to re-run the evaluation:

```bash
python evals/asl_eval.py
```

### 2. Review Entries

To manually review entries that need human judgment:

```bash
python evals/review_eval.py
```

This will:
1. Load the evaluation results from `asl_eval_results.json`
2. Display a summary of the evaluation status
3. Allow you to review each entry marked as "Needs Human Review"
4. Save the updated results to `asl_eval_reviewed.json`

### 3. Batch Review

To review multiple entries at once based on criteria:

```bash
python evals/batch_review.py --min-bert 0.85 --max-bert 0.88 --action pass
```

Options:
- `--input`: Input JSON file (default: `asl_eval_results.json`)
- `--output`: Output JSON file (default: `asl_eval_reviewed.json`)
- `--min-bert`: Minimum BERTScore for batch review (default: 0.85)
- `--max-bert`: Maximum BERTScore for batch review (default: 0.88)
- `--action`: Action to take for entries in the BERTScore range (pass, fail, skip)
- `--keyword`: Keyword to search for in questions and responses
- `--keyword-action`: Action to take for entries containing the keyword (pass, fail, skip)

Examples:

```bash
# Pass all entries with BERTScore between 0.85 and 0.88
python evals/batch_review.py --min-bert 0.85 --max-bert 0.88 --action pass

# Fail all entries containing the word "morale" in the question or response
python evals/batch_review.py --keyword morale --keyword-action fail

# Combine both criteria
python evals/batch_review.py --min-bert 0.85 --max-bert 0.88 --action pass --keyword morale --keyword-action fail
```

### 4. Generate Report

To generate an HTML report of the evaluation results:

```bash
python evals/generate_report.py
```

Options:
- `--input`: Input JSON file with reviewed results (default: `asl_eval_reviewed.json`)
- `--output`: Output HTML report file (default: `asl_eval_report.html`)

The report includes:
- Summary of evaluation metrics
- Pass rate and distribution of evaluation statuses
- Detailed view of each question, expected answer, and model response
- Sorting by evaluation status and BERTScore

## Workflow

1. Run the evaluation to generate `asl_eval_results.json`
2. Use batch review to handle groups of similar entries
3. Use manual review for remaining entries that need human judgment
4. Generate a report to visualize the results

## Metrics

The evaluation uses two main metrics:
- **RougeL**: Measures the longest common subsequence between the model response and expected answer
- **BERTScore**: Measures semantic similarity using BERT embeddings

Automatic evaluation thresholds:
- Auto-pass: BERTScore ≥ 0.9
- Auto-fail: BERTScore < 0.7
- Needs Human Review: 0.7 ≤ BERTScore < 0.9 