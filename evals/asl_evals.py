import json
import evaluate
import os
import sys
import time
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Check if the evaluation dataset exists
if not os.path.exists("evals/asl_evals.jsonl"):
    print("Error: evals/asl_evals.jsonl file not found. Please make sure it exists in the evals directory.")
    sys.exit(1)

try:
    # Load the evaluation dataset with detailed logging
    eval_data = []
    with open("evals/asl_evals.jsonl", "r") as f:
        for line_number, line in enumerate(f, start=1):
            try:
                eval_data.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON on line {line_number}: {e}")
                sys.exit(1)
except Exception as e:
    print(f"Error loading evaluation data: {str(e)}")
    sys.exit(1)

try:
    # Initialize Hugging Face evaluate metrics
    rouge = evaluate.load("rouge")
    bertscore = evaluate.load("bertscore")
except Exception as e:
    print(f"Error loading evaluation metrics: {str(e)}")
    print("Make sure you have installed the required packages with:")
    print("pip install evaluate")
    print("pip install rouge_score")
    print("pip install bert_score")
    sys.exit(1)

# Get OpenAI API key from environment
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    print("Error: OPENAI_API_KEY environment variable not set.")
    print("Please set your OpenAI API key with:")
    print("export OPENAI_API_KEY='your-api-key'")
    sys.exit(1)

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

# Use the Assistant ID from your main application
ASSISTANT_ID = "asst_M65nFsVKjQRamCQrfHThTeJt"

# Set BERTScore threshold values
BERT_PASS_THRESHOLD = 0.88
BERT_REVIEW_THRESHOLD = 0.80

def get_model_response(question):
    """
    Function to get responses from OpenAI Assistant API
    
    Args:
        question: The question to ask the assistant
        
    Returns:
        The assistant's response as a string
    """
    try:
        # Create a thread
        thread = client.beta.threads.create()
        
        # Add a message to the thread
        message = client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=question
        )
        
        # Run the assistant
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID
        )
        
        # Wait for the run to complete
        while run.status in ["queued", "in_progress"]:
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            
        # Check if run completed successfully
        if run.status != "completed":
            print(f"Run failed with status: {run.status}")
            return f"Error: Assistant run failed with status {run.status}"
            
        # Get the assistant's response
        messages = client.beta.threads.messages.list(
            thread_id=thread.id
        )
        
        # Extract the assistant's response
        for msg in messages.data:
            if msg.role == "assistant":
                # Get the text content
                for content_item in msg.content:
                    if content_item.type == "text":
                        return content_item.text.value
                        
        return "No response from assistant"
        
    except Exception as e:
        print(f"Error getting response from OpenAI Assistant: {str(e)}")
        return f"Error: {str(e)}"

def auto_evaluate(bert_f1):
    """
    Automatically evaluate a response based on BERTScore
    
    Args:
        bert_f1: BERTScore F1 value
        
    Returns:
        Evaluation string
    """
    if bert_f1 >= BERT_PASS_THRESHOLD:
        return "✅ Auto-pass"
    elif bert_f1 >= BERT_REVIEW_THRESHOLD:
        return "⚠️ Needs Human Review"
    else:
        return "❌ Auto-fail"

# Function to run evaluation
def evaluate_model(get_model_response, eval_data):
    """
    Evaluate model performance using HuggingFace metrics
    
    Args:
        get_model_response: A function that takes a question string and returns a response string
        eval_data: List of evaluation examples with 'question' and 'expected_answer' fields
    """
    results = []

    for i, entry in enumerate(eval_data):
        try:
            if "question" not in entry or "expected_answer" not in entry:
                print(f"Warning: Entry {i} is missing required fields. Skipping.")
                continue
                
            question = entry["question"]
            expected_answer = entry["expected_answer"]

            print(f"\nProcessing question {i+1}/{len(eval_data)}: {question[:50]}...")
            
            # Get response from the model using the provided function
            response = get_model_response(question)
            
            if not isinstance(response, str):
                print(f"Warning: Response for question {i} is not a string. Converting to string.")
                response = str(response)

            print(f"Got response: {response[:50]}...")

            # Compute ROUGE-L score
            rouge_result = rouge.compute(predictions=[response], references=[expected_answer], rouge_types=["rougeL"])

            # Compute BERTScore
            bert_result = bertscore.compute(predictions=[response], references=[expected_answer], lang="en")
            
            # Get auto-evaluation
            bert_f1 = bert_result["f1"][0]
            evaluation = auto_evaluate(bert_f1)

            # Store results
            results.append({
                "question": question,
                "expected_answer": expected_answer,
                "model_response": response,
                "rougeL": rouge_result["rougeL"],
                "bert_f1": bert_f1,
                "evaluation": evaluation
            })
            
            # Print progress and scores
            print(f"ROUGE-L: {rouge_result['rougeL']}, BERTScore: {bert_f1}")
            print(f"Evaluation: {evaluation}")
            
        except Exception as e:
            print(f"\nError processing entry {i}: {str(e)}")
            continue

    print("\nEvaluation completed successfully.")
    return results

try:
    # Run the evaluation
    results = evaluate_model(get_model_response, eval_data)

    # Save detailed results
    with open("asl_eval_results.json", "w") as f:
        json.dump(results, f, indent=4)

    # Print summary statistics
    pass_count = sum(1 for r in results if r["evaluation"] == "✅ Auto-pass")
    review_count = sum(1 for r in results if r["evaluation"] == "⚠️ Needs Human Review")
    fail_count = sum(1 for r in results if r["evaluation"] == "❌ Auto-fail")
    
    print("\n===== EVALUATION SUMMARY =====")
    print(f"Total questions: {len(results)}")
    print(f"Auto-pass: {pass_count} ({pass_count/len(results)*100:.1f}%)")
    print(f"Needs review: {review_count} ({review_count/len(results)*100:.1f}%)")
    print(f"Auto-fail: {fail_count} ({fail_count/len(results)*100:.1f}%)")
    print("=============================")
    
    print("Evaluation complete. Results saved to asl_eval_results.json")
except Exception as e:
    print(f"Error during evaluation: {str(e)}")
    sys.exit(1)

# Load and validate JSONL file
with open("evals/asl_evals.jsonl", "r") as file:
    for line_number, line in enumerate(file, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON on line {line_number}: {e}")
            raise
