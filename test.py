import os
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=api_key)

# Test API call
try:
    response = client.models.list()
    print("✅ OpenAI API connection successful!")
    print("Available models:", [model.id for model in response])
except Exception as e:
    print("❌ OpenAI API connection failed:", e)
