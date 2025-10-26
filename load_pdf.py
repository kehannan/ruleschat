#!/usr/bin/env python3
"""
Script to load the ASL rules PDF file using OpenAI's new file upload API.
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def load_asl_pdf():
    """Load the ASL rules PDF file to OpenAI."""
    
    # Initialize OpenAI client
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    # Path to the PDF file
    pdf_path = "evals/sources/eASLRB_v2.12-INHERIT_ZOOM_unlocked.pdf"
    
    # Check if file exists
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found at {pdf_path}")
        return None
    
    # Get file size
    file_size = os.path.getsize(pdf_path) / (1024 * 1024)  # Convert to MB
    print(f"PDF file size: {file_size:.1f} MB")
    
    if file_size > 100:
        print("Error: File is larger than 100MB limit")
        return None
    
    try:
        # Upload the PDF file
        print("Uploading PDF file to OpenAI...")
        with open(pdf_path, "rb") as file:
            response = client.files.create(
                file=file,
                purpose="assistants"
            )
        
        file_id = response.id
        print(f"Successfully uploaded PDF file. File ID: {file_id}")
        
        # Get file details
        file_info = client.files.retrieve(file_id)
        print(f"File name: {file_info.filename}")
        print(f"File size: {file_info.bytes} bytes")
        print(f"File status: {file_info.status}")
        
        return file_id
        
    except Exception as e:
        print(f"Error uploading file: {e}")
        return None

if __name__ == "__main__":
    file_id = load_asl_pdf()
    if file_id:
        print(f"\nFile ID to use in your app: {file_id}")
        # Save file ID to a file for later use
        with open("asl_pdf_file_id.txt", "w") as f:
            f.write(file_id)
        print("File ID saved to asl_pdf_file_id.txt")
    else:
        print("Failed to load PDF file.") 