"""Configuration management for ASL service."""
import os
import json
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from app.config import ASL_SYSTEM_INSTRUCTIONS, DEFAULT_MODEL, TEMPERATURE


@dataclass
class ASLConfig:
    """Configuration for ASL service."""
    api_key: str
    org_id: Optional[str]
    project_id: Optional[str]
    vector_store_id: str
    model: str
    temperature: float
    system_instructions: str


def _load_vector_store_id(config_file: Optional[str] = None) -> Optional[str]:
    """
    Load vector store ID from config file.
    
    Args:
        config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        
    Returns:
        Vector store ID or None if not found
    """
    if config_file is None:
        config_file = "responses_api_config.json"
    
    config_path = Path(config_file)
    if not config_path.exists():
        logging.warning(f"⚠️ Config file not found: {config_path}")
        return None
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        
        # Handle versioned config format
        if "versions" in config and "active_version" in config:
            active_version = config.get("active_version")
            if active_version and active_version in config["versions"]:
                version_data = config["versions"][active_version]
                vector_store_id = version_data.get("vector_store_id")
                if vector_store_id:
                    logging.info(f"✅ Loaded vector store ID from versioned config: {active_version}")
                    return vector_store_id
        
        # Handle legacy format
        vector_store_id = config.get("vector_store_id")
        if vector_store_id:
            logging.info("✅ Loaded vector store ID from legacy config")
            return vector_store_id
        
        logging.warning("⚠️ No vector_store_id found in config file")
        return None
        
    except Exception as e:
        logging.error(f"❌ Error loading config file: {e}")
        return None


def load_asl_config(
    api_key: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    config_file: Optional[str] = None
) -> ASLConfig:
    """
    Load ASL configuration from environment variables and config file.
    
    Args:
        api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
        vector_store_id: Vector store ID (defaults to loading from config file)
        config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        
    Returns:
        ASLConfig instance
    """
    # Load API key
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
    
    # Load organization and project IDs
    org_id = os.getenv("OPENAI_ORG_ID")
    project_id = os.getenv("OPENAI_PROJECT_ID")
    
    # Load vector store ID
    if vector_store_id is None:
        vector_store_id = _load_vector_store_id(config_file)
        if not vector_store_id:
            raise ValueError("vector_store_id not found in config file or environment")
    
    # Load model and temperature
    model = os.getenv("DEFAULT_MODEL", DEFAULT_MODEL)
    temperature = float(os.getenv("TEMPERATURE", str(TEMPERATURE)))
    
    # Load system instructions
    system_instructions = os.getenv("ASL_SYSTEM_INSTRUCTIONS", ASL_SYSTEM_INSTRUCTIONS)
    
    return ASLConfig(
        api_key=api_key,
        org_id=org_id,
        project_id=project_id,
        vector_store_id=vector_store_id,
        model=model,
        temperature=temperature,
        system_instructions=system_instructions
    )

