# Setup

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

or install everything from the service requirements file:

```bash
pip install -r mcp_service/requirements.txt
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

