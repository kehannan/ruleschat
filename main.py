# main.py
import os
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from openai import OpenAI
from assistant import EventHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
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
async def get_ruleschat(request: Request):
    return templates.TemplateResponse("ruleschat.html", {"request": request})

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    logging.info("WebSocket connection established.")
    await websocket.accept()
    while True:
        # Read user’s question
        question = await websocket.receive_text()
        logging.info(f"Received question: {question}")

        # Create a new thread and message
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=question
        )
        logging.info("Thread and user message created.")

        # Stream the assistant's answer
        event_handler = EventHandler(websocket)  # Log inside the event handler too
        logging.info("Starting OpenAI streaming response.")
        with client.beta.threads.runs.stream(
            thread_id=thread.id,
            assistant_id=assistant.id,
            instructions="Please address the user as Jane Doe. The user has a premium account.",
            event_handler=event_handler,
        ) as stream:
            stream.until_done()

        logging.info("Finished streaming response.")