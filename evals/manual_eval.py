import json
import os
import sys
import time
from openai import OpenAI
from dotenv import load_dotenv
import traceback, io
import requests
import argparse

# Load environment variables
load_dotenv()

# Initialize OpenAI client with the same configuration as the working app
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(
    api_key=openai_api_key,
    base_url="https://api.openai.com/v1",
    default_headers={"OpenAI-Beta": "assistants=v2"}
)
ASSISTANT_ID = "asst_M65nFsVKjQRamCQrfHThTeJt"

def get_assistant_response(question):
    """Get a response from the OpenAI Assistant"""
    try:
        # Create a thread
        thread = client.beta.threads.create()
        
        # Add the user's message to the thread
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=question
        )
        
        # Run the assistant
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID
        )
        
        # Wait for completion with timeout
        start_time = time.time()
        timeout = 60  # 60 second timeout
        
        while True:
            if time.time() - start_time > timeout:
                return "Error: Request timed out after 60 seconds"
                
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            
            if run.status == "completed":
                break
            elif run.status in ["failed", "cancelled", "expired"]:
                # Include detailed error information if available
                if getattr(run, "last_error", None):
                    err_code = run.last_error.get("code", "") if isinstance(run.last_error, dict) else getattr(run.last_error, "code", "")
                    err_msg = run.last_error.get("message", "") if isinstance(run.last_error, dict) else getattr(run.last_error, "message", "")
                    # Retry up to 3 times on rate limit errors
                    if err_code == "rate_limit_exceeded":
                        retries = getattr(get_assistant_response, "_rate_retries", 0)
                        if retries < 3:
                            setattr(get_assistant_response, "_rate_retries", retries + 1)
                            wait_time = 5 * (retries + 1)
                            time.sleep(wait_time)
                            return get_assistant_response(question)
                    return f"Error: Run failed ({err_code}) - {err_msg}"
                return f"Error: Run failed with status {run.status}"
            
            time.sleep(1)
        
        # Work around OpenAI Python client bug by using direct HTTP request
        headers = {
            "Authorization": f"Bearer {openai_api_key}",
            "OpenAI-Beta": "assistants=v2",
            "Content-Type": "application/json"
        }

        url = f"https://api.openai.com/v1/threads/{thread.id}/messages"
        try:
            resp = requests.get(url, headers=headers, params={"limit": 20})
            if resp.status_code != 200:
                return f"Error: Failed to fetch messages (status {resp.status_code}): {resp.text}"
            data = resp.json()
            if "data" not in data or len(data["data"]) == 0:
                return "Error: No messages found in thread"
            # Filter assistant messages
            assistant_messages = [m for m in data["data"] if m.get("role") == "assistant"]
            if not assistant_messages:
                return "Error: No assistant response found"
            # The list is typically newest first
            first_msg = assistant_messages[0]
            # Each message content is a list of dicts
            contents = first_msg.get("content", [])
            text_chunks = [c.get("text", {}).get("value", "") for c in contents if c.get("type") == "text"]
            if not text_chunks:
                return "Error: No text content found in assistant message"
            return "\n".join(text_chunks).strip()
        except Exception as e:
            # Fallback to previous method as last resort
            try:
                messages = client.beta.threads.messages.list(thread_id=thread.id)
                assistant_messages = [msg for msg in messages.data if msg.role == "assistant"]
                if assistant_messages:
                    contents = [content for content in assistant_messages[0].content if content.type == "text"]
                    if contents:
                        return contents[0].text.value
            except Exception:
                pass
            return f"Error: {str(e)}"
        
    except Exception as e:
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        trace = buf.getvalue()
        return f"Error: {str(e)}\nTraceback:\n{trace}"

def manual_evaluate(start_index: int = 1):
    """Run manual evaluation of ASL questions"""
    try:
        # Load evaluation data
        with open("asl_evals.jsonl", "r", encoding='utf-8') as f:
            eval_data = [json.loads(line) for line in f]
        
        results = []
        total = len(eval_data)
        
        print(f"\nLoaded {total} questions for evaluation.")
        print("\nStarting manual evaluation session...")
        print("For each question, you'll see:")
        print("1. The section reference")
        print("2. The question")
        print("3. The expected answer")
        print("4. The assistant's response")
        print("\nYou'll then be asked to judge if the response is correct.")
        input("\nPress Enter to begin...")
        
        for i, entry in enumerate(eval_data, 1):
            if i < start_index:
                continue  # Skip until we reach the desired start index
            print(f"\n{'='*80}")
            print(f"\nQuestion {i}/{total}")
            print(f"\nSection: {entry['section']}")
            print(f"\nQuestion:\n{entry['question']}")
            
            # Get assistant's response
            print("\nGetting assistant's response...")
            response = get_assistant_response(entry['question'])
            
            print(f"\nExpected Answer:\n{entry['expected_answer']}")
            print(f"\nAssistant's Response:\n{response}")
            
            # Get manual judgment
            while True:
                judgment = input("\nIs the response correct? (y/n/p/q)\n"
                               "y = Yes, correct\n"
                               "n = No, incorrect\n"
                               "p = Partially correct\n"
                               "q = Quit evaluation\n"
                               "> ").lower().strip()
                
                if judgment in ['y', 'n', 'p', 'q']:
                    break
                print("\nInvalid input. Please try again.")
            
            if judgment == 'q':
                print("\nEvaluation session ended by user.")
                break
            
            # Get comments if needed
            comments = ""
            if judgment in ['n', 'p']:
                comments = input("\nPlease provide comments about what was wrong/missing:\n> ").strip()
            
            # Store result
            results.append({
                "section": entry['section'],
                "question": entry['question'],
                "expected_answer": entry['expected_answer'],
                "assistant_response": response,
                "judgment": {
                    'y': 'correct',
                    'n': 'incorrect',
                    'p': 'partial'
                }[judgment],
                "comments": comments
            })
            
            # Save progress after each evaluation
            with open("manual_eval_results.jsonl", "w", encoding='utf-8') as f:
                for result in results:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
        
        # Print summary
        total_evaluated = len(results)
        if total_evaluated > 0:
            correct = sum(1 for r in results if r['judgment'] == 'correct')
            partial = sum(1 for r in results if r['judgment'] == 'partial')
            incorrect = sum(1 for r in results if r['judgment'] == 'incorrect')
            
            print("\n" + "="*80)
            print("\nEvaluation Summary:")
            print(f"Total questions evaluated: {total_evaluated}")
            print(f"Correct: {correct} ({correct/total_evaluated*100:.1f}%)")
            print(f"Partially correct: {partial} ({partial/total_evaluated*100:.1f}%)")
            print(f"Incorrect: {incorrect} ({incorrect/total_evaluated*100:.1f}%)")
            print(f"\nDetailed results saved to: manual_eval_results.jsonl")
        
    except KeyboardInterrupt:
        print("\n\nEvaluation interrupted by user.")
    except Exception as e:
        print(f"\nError during evaluation: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manually evaluate ASL assistant responses.")
    parser.add_argument("--start", type=int, default=1, help="Start at this question number (1-indexed).")
    args = parser.parse_args()

    # Change to the evals directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    manual_evaluate(start_index=max(1, args.start)) 