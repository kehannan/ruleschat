#!/usr/bin/env python3
"""
Script to show a random sample chunk from the vector store.
Since OpenAI doesn't expose individual chunks directly, we'll query
the vector store with a random query to see what content is retrieved.
"""

import os
import json
import random
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

def load_vector_store_config():
    """Load vector store configuration from responses_api_config.json"""
    config_path = Path("responses_api_config.json")
    if not config_path.exists():
        raise FileNotFoundError("responses_api_config.json not found")
    
    with open(config_path, "r") as f:
        config = json.load(f)
    
    # Get active version
    active_version = config.get("active_version")
    if not active_version:
        raise ValueError("No active version found in config")
    
    version_data = config["versions"].get(active_version)
    if not version_data:
        raise ValueError(f"Version {active_version} not found in config")
    
    return version_data.get("vector_store_id"), version_data

def get_sample_from_vector_store(client, vector_store_id: str, num_samples: int = 3):
    """Query the vector store with random queries to see sample content"""
    
    # Random queries that should retrieve different types of content
    random_queries = [
        "What is a rule about movement?",
        "Tell me about combat",
        "What does section A4 say?",
        "Explain a basic rule",
        "What is covered in the rules?",
        "Show me an example rule",
        "What are the rules for units?",
        "Tell me about terrain",
        "What is section B1?",
        "Explain fire combat",
        "What are the rules for leaders?",
        "Tell me about morale",
    ]
    
    # Get multiple random samples
    selected_queries = random.sample(random_queries, min(num_samples, len(random_queries)))
    
    for i, query in enumerate(selected_queries, 1):
        print(f"\n🔍 Sample {i}/{num_samples}: Querying with '{query}'")
        print("=" * 80)
        
        try:
            # Use Responses API to query the vector store
            # Ask explicitly to show the raw chunk format with metadata
            enhanced_query = f"{query} Show me the exact chunk content including the section and page metadata in the format {{section|page}}."
            response = client.responses.create(
                model=os.getenv("DEFAULT_MODEL", "gpt-4o"),
                input=enhanced_query,
                instructions="You are retrieving chunks from the ASL rulebook vector store. Each chunk has metadata in the format {section_id|page_num} at the beginning, like {A4.1|48}. When you retrieve content, preserve and display this metadata format exactly as it appears in the chunks. Show the section ID and page number for each piece of content.",
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                }]
            )
            
            if response.output_text:
                print("\n📄 Content Retrieved from Vector Store:")
                print("-" * 80)
                print(response.output_text)
                print("-" * 80)
                
                # Try to extract section and page info from the response
                import re
                section_page_pattern = r'\{([A-Z]\d+\.\d+(?:\.\d+)?)\|(\d+)\}'
                matches = re.findall(section_page_pattern, response.output_text)
                if matches:
                    print("\n📌 Section and Page References Found:")
                    for section_id, page_num in matches:
                        print(f"   Section: {section_id} | Page: {page_num}")
                else:
                    # Try to find just section references
                    section_pattern = r'\b([A-Z]\d+\.\d+(?:\.\d+)?)\b'
                    section_matches = re.findall(section_pattern, response.output_text)
                    if section_matches:
                        print(f"\n📌 Section References Found: {', '.join(set(section_matches))}")
            else:
                print("⚠️ No response content found")
                
        except Exception as e:
            print(f"❌ Error querying vector store: {e}")
        
        if i < num_samples:
            print()
    
    print(f"\n✅ Retrieved {num_samples} sample(s) from vector store")

def main():
    """Main function"""
    print("🚀 Vector Store Sample Viewer")
    print("=" * 80)
    
    try:
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in .env file")
        
        client = OpenAI(
            api_key=api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID")
        )
        
        # Load vector store configuration
        vector_store_id, version_data = load_vector_store_config()
        print(f"\n📚 Vector Store ID: {vector_store_id}")
        print(f"📋 Version: {version_data.get('chunking_method', 'unknown')}")
        if 'total_chunks' in version_data:
            print(f"📊 Total Chunks: {version_data['total_chunks']}")
        if 'total_sections' in version_data:
            print(f"📑 Total Sections: {version_data['total_sections']}")
        print()
        
        # Get sample content (3 random samples)
        get_sample_from_vector_store(client, vector_store_id, num_samples=3)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

