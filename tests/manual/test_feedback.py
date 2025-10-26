#!/usr/bin/env python3
"""
Test script for the feedback system
"""
import json
from models import save_feedback

def test_feedback_save():
    """Test saving feedback to both database and JSONL file"""
    try:
        # Test data
        test_feedback = save_feedback(
            user_id=None,  # Anonymous user
            session_id="test_session_123",
            thread_id="thread_test_456",
            question="What is the rule for line of sight?",
            assistant_response="Line of sight is determined by drawing a straight line from the center of the firing unit's hex to the center of the target hex.",
            rating="up",
            better_answer=None
        )
        
        print(f"✅ Feedback saved successfully with ID: {test_feedback.id}")
        
        # Test with better answer
        test_feedback2 = save_feedback(
            user_id=None,
            session_id="test_session_123",
            thread_id="thread_test_456",
            question="What is the rule for movement?",
            assistant_response="Movement is determined by the unit's movement allowance.",
            rating="down",
            better_answer="Movement is determined by the unit's movement allowance, but terrain costs and other factors may apply."
        )
        
        print(f"✅ Feedback with better answer saved successfully with ID: {test_feedback2.id}")
        
        # Check if JSONL file was created
        import os
        if os.path.exists("feedback_results.jsonl"):
            print("✅ feedback_results.jsonl file created successfully")
            
            # Read and display the contents
            with open("feedback_results.jsonl", "r") as f:
                lines = f.readlines()
                print(f"📄 File contains {len(lines)} feedback entries:")
                for i, line in enumerate(lines, 1):
                    data = json.loads(line.strip())
                    print(f"  {i}. Rating: {data['rating']}, Question: {data['question'][:50]}...")
        else:
            print("❌ feedback_results.jsonl file not found")
            
    except Exception as e:
        print(f"❌ Error testing feedback: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_feedback_save() 