"""JSONL file logger for chat Q&A interactions."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("data/logs")
LOG_FILE = LOG_DIR / "chat_log.jsonl"


def append_chat_log(
    user_email: str,
    question: str,
    answer: str,
    model: str,
    timing_data: dict,
    image_paths: list = None,
    vsav_paths: list = None,
):
    """Append a single Q&A interaction as one JSON line."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_email": user_email,
            "question": question,
            "answer": answer[:2000],
            "model": model,
            "input_tokens": timing_data.get("input_tokens"),
            "output_tokens": timing_data.get("output_tokens"),
            "ttft_ms": timing_data.get("ttft_ms"),
            "total_time_ms": timing_data.get("total_time_ms"),
            "file_search_time_ms": timing_data.get("file_search_time_ms"),
            "image_paths": image_paths or None,
            "vsav_paths": vsav_paths or None,
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.error(f"Failed to write chat log: {e}")
