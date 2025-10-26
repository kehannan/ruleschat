# ASL Rules Assistant

A FastAPI web application that helps Advanced Squad Leader (ASL) players understand and apply the complex rules of the game using AI assistance powered by OpenAI.

## Features

- **AI-Powered Rules Assistant**: Ask questions about ASL rules and get accurate answers
- **Vector Store Integration**: Searches through the official ASL rulebook
- **User Authentication**: Secure login and user management
- **Invitation System**: Admin-managed user invitations
- **WebSocket Chat**: Real-time streaming responses
- **Feedback System**: Users can provide feedback on answers

## Related Repositories

- **[mysite2-evals-sft](https://github.com/kehannan/mysite2-evals-sft)**: Evaluation datasets, fine-tuning data, and data processing scripts

## Setup

## Environment Variables

The application relies on the following environment variables:

- `SECRET_KEY` – secret key used for signing JWT tokens.
- `OPENAI_API_KEY` – API key for communicating with OpenAI.
- `ADMIN_USERNAME` – optional username for the site administrator.

Create a `.env` file in the project root and set these values.

## Installation

Install the required Python packages. You can either install them individually:

```bash
pip install fastapi uvicorn sqlalchemy passlib[bcrypt] python-jose python-dotenv openai
```

or install everything from the requirements file:

```bash
pip install -r requirements.txt
```

## Create the Admin User

Run the following command and follow the prompts to create the initial admin account:

```bash
python create_user.py
```

## Running the Server

Start the FastAPI development server with:

```bash
uvicorn main:app --reload
```

