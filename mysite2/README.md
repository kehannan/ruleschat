# SportsMaps API

A FastAPI backend for the SportsMaps application, for mapping and exploring sports venues.

## Setup

1. Create and activate the conda environment:
```bash
conda create -n mysite2_env python=3.13
conda activate mysite2_env
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the development server:
```bash
uvicorn main:app --reload
```

## Project Structure

```
mysite2/
├── routers/       # API route handlers
│   └── invite.py  # Invitation system endpoints
├── main.py        # FastAPI application setup
├── models.py      # SQLAlchemy database models
├── database.py    # Database connection setup
└── requirements.txt # Project dependencies
```

## Features

- RESTful API built with FastAPI
- SQLAlchemy ORM for database interactions
- User invitation system
- API documentation with Swagger UI (available at /docs)

## API Documentation

When the server is running, visit http://localhost:8000/docs for interactive API documentation.

## License

MIT License 