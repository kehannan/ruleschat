"""Configuration loading for ASL service."""
import os
import json
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE


@dataclass
class ASLConfig:
    """ASL service configuration."""
    api_key: str
    vector_store_id: str
    model: str
    temperature: float
    system_instructions: str
    org_id: Optional[str] = None
    project_id: Optional[str] = None


def load_vector_store_id(config_file: Optional[str] = None) -> Optional[str]:
    """Load vector store ID from config file (supports versioned config)."""
    if config_file:
        config_path = Path(config_file)
    else:
        # Default location: responses_api_config.json in project root
        config_path = Path(__file__).parent.parent.parent / "responses_api_config.json"
    
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                
                # Check if versioned config format
                if "versions" in config:
                    active_version = config.get("active_version")
                    if active_version and active_version in config["versions"]:
                        version_data = config["versions"][active_version]
                        vector_store_id = version_data.get("vector_store_id")
                        logging.info(f"Loaded vector store ID from versioned config (active: {active_version})")
                        return vector_store_id
                    else:
                        logging.warning(f"Active version '{active_version}' not found in config")
                        return None
                else:
                    # Legacy format (backward compatibility)
                    return config.get("vector_store_id")
        except Exception as e:
            logging.error(f"Error loading config file: {e}")
    else:
        logging.warning(f"Config file not found: {config_path}")
    
    return None


def load_asl_config(
    api_key: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    config_file: Optional[str] = None
) -> ASLConfig:
    """
    Load ASL service configuration.
    
    Args:
        api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
        vector_store_id: Vector store ID (defaults to loading from config file)
        config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        
    Returns:
        ASLConfig instance
        
    Raises:
        ValueError: If api_key or vector_store_id is missing
    """
    # Load API key
    resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise ValueError("OpenAI API key is required")
    
    # Load vector store ID
    if vector_store_id:
        resolved_vector_store_id = vector_store_id
    else:
        resolved_vector_store_id = load_vector_store_id(config_file)
    
    if not resolved_vector_store_id:
        raise ValueError("Vector store ID is required. Please configure responses_api_config.json")
    
    # Load optional org/project IDs
    org_id = os.getenv("OPENAI_ORG_ID")
    project_id = os.getenv("OPENAI_PROJECT_ID")
    
    return ASLConfig(
        api_key=resolved_api_key,
        vector_store_id=resolved_vector_store_id,
        model=DEFAULT_MODEL,
        temperature=TEMPERATURE,
        system_instructions=ASL_SYSTEM_INSTRUCTIONS,
        org_id=org_id,
        project_id=project_id
    )

