# assistant.py
import logging
import asyncio
from openai import AssistantEventHandler

class EventHandler(AssistantEventHandler):
    def __init__(self, websocket):
        super().__init__()
        self.websocket = websocket

    def on_text_created(self, text) -> None:
        logging.info(f"on_text_created: {text}")
        asyncio.create_task(self.websocket.send_text("\nassistant > "))

    def on_text_delta(self, delta, snapshot):
        asyncio.create_task(self.websocket.send_text(delta.value))
        # Force an actual pause for I/O to flush
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), asyncio.get_event_loop())


        
