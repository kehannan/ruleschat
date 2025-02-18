# main.py
import os
import sys
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from openai import OpenAI
from assistant import EventHandler

# Configure logging with forced flush
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=openai_api_key)
assistant_id = "asst_M65nFsVKjQRamCQrfHThTeJt"
assistant = client.beta.assistants.retrieve(assistant_id)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Home route
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

# Rules chat route
@app.get("/ruleschat", response_class=HTMLResponse)
async def ruleschat(request: Request):
    return templates.TemplateResponse("ruleschat.html", {"request": request})

@app.websocket("/ws/chat/")
async def websocket_chat(websocket: WebSocket):
    logging.info("🔹 WebSocket connection established.")
    await websocket.accept()

    try:
        # Create a single persistent OpenAI thread for the session
        thread = client.beta.threads.create()
        logging.info(f"🆕 Created persistent thread: {thread.id}")

        while True:  # Keep connection open for multiple interactions
            question = await websocket.receive_text()
            logging.info(f"✅ Received question: {question}")

            # Add the new message to the existing OpenAI thread
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=question
            )
            logging.info(f"📩 Added message to thread {thread.id}")

            # Start streaming the assistant's response
            logging.info("🟢 Starting OpenAI response stream...")
            with client.beta.threads.runs.stream(
                thread_id=thread.id,
                assistant_id=assistant.id,
                instructions="..."
            ) as stream:
                for chunk in stream:
                    if chunk.event == "thread.message.delta":
                        for content_block in chunk.data.delta.content:
                            if content_block.type == "text":
                                text = content_block.text.value
                                await websocket.send_text(text)
                                logging.info(f"📤 Sent chunk: {text}")
                                sys.stdout.flush()

            logging.info("✅ Finished streaming response. Waiting for the next message...")

    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}")
    finally:
        logging.info("🔻 Closing WebSocket connection.")
        await websocket.close()