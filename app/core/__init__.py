"""Core utilities and configurations.

No eager submodule imports: auth pulls in the whole web stack
(fastapi/jose/sqlalchemy), which breaks standalone users of
app.core.observability such as the eval harness. Import submodules
directly (e.g. `from app.core import auth`).
"""
