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
    qa_vector_store_id: Optional[str]
    model: str
    temperature: float
    system_instructions: str

    @property
    def all_vector_store_ids(self) -> list[str]:
        """Vector stores to pass to file_search (rulebook + optional Q&A errata)."""
        ids = [self.vector_store_id]
        if self.qa_vector_store_id:
            ids.append(self.qa_vector_store_id)
        return ids


def _read_config_json(config_file: Optional[str] = None) -> Optional[dict]:
    if config_file is None:
        config_file = "responses_api_config.json"
    config_path = Path(config_file)
    if not config_path.exists():
        logging.warning(f"⚠️ Config file not found: {config_path}")
        return None
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"❌ Error loading config file: {e}")
        return None


def _load_vector_store_id(config_file: Optional[str] = None) -> Optional[str]:
    """Load the active rulebook vector store ID from config."""
    config = _read_config_json(config_file)
    if config is None:
        return None

    if "versions" in config and "active_version" in config:
        active_version = config.get("active_version")
        if active_version and active_version in config["versions"]:
            vector_store_id = config["versions"][active_version].get("vector_store_id")
            if vector_store_id:
                logging.info(f"✅ Loaded vector store ID from versioned config: {active_version}")
                return vector_store_id

    vector_store_id = config.get("vector_store_id")
    if vector_store_id:
        logging.info("✅ Loaded vector store ID from legacy config")
        return vector_store_id

    logging.warning("⚠️ No vector_store_id found in config file")
    return None


def _load_qa_vector_store_id(config_file: Optional[str] = None) -> Optional[str]:
    """Load the active Q&A errata vector store ID from config. Optional — None if not present."""
    config = _read_config_json(config_file)
    if config is None:
        return None

    versions = config.get("qa_versions") or {}
    active = config.get("active_qa_version")
    if active and active in versions:
        vs_id = versions[active].get("vector_store_id")
        if vs_id:
            logging.info(f"✅ Loaded Q&A vector store ID: {active}")
            return vs_id

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

    # Load Q&A errata vector store ID (optional)
    qa_vector_store_id = _load_qa_vector_store_id(config_file)

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
        qa_vector_store_id=qa_vector_store_id,
        model=model,
        temperature=temperature,
        system_instructions=system_instructions
    )

