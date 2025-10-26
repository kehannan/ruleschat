import os
from dotenv import load_dotenv

load_dotenv()

ASL_SYSTEM_INSTRUCTIONS = os.getenv("ASL_SYSTEM_INSTRUCTIONS", "You are an expert on Advanced Squad Leader (ASL) rules.")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4o")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
WEBSOCKET_PING_INTERVAL = int(os.getenv("WEBSOCKET_PING_INTERVAL", "30"))
STREAMING_DELAY = float(os.getenv("STREAMING_DELAY", "0.01")) 