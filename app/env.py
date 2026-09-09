"""Environment loading — one place, one precedence order.

API keys live in a single file shared by every project on this machine,
``~/.config/secrets/keys.env`` (mode 600), so a rotated key is fixed once.
Precedence, first definition wins (``override=False`` throughout):

  1. the shell — direnv exports keys.env on ``cd`` via the gitignored .envrc
  2. the shared keys file, for launchers that bypass the shell
     (desktop-app dev server, systemd, ``python run.py`` from a plain shell)
  3. this repo's ``.env`` — project-local settings (SECRET_KEY, mail, …)

Prod has no shared file, so step 2 is skipped and ``.env`` behaves as before.
``SHARED_KEYS_ENV`` points at a different shared file if needed.
"""
import logging
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

SHARED_KEYS_FILE = Path(
    os.getenv("SHARED_KEYS_ENV", "~/.config/secrets/keys.env")
).expanduser()

_REPO_ENV = Path(__file__).resolve().parents[1] / ".env"
_loaded = False


def load_env() -> None:
    """Idempotent; safe to call from every module that used to call load_dotenv()."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if _exists(SHARED_KEYS_FILE):
        _load(SHARED_KEYS_FILE)
    repo_env = _REPO_ENV if _exists(_REPO_ENV) else find_dotenv(usecwd=True)
    if repo_env:
        _load(Path(repo_env))


def _exists(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:  # EPERM from a sandbox — treat as present, _load reports
        return True


def _load(path: Path) -> None:
    """Load one file; an unreadable file (sandboxed test runs, wrong mode)
    is a warning, not a crash — the vars may already be in the shell."""
    try:
        load_dotenv(path, override=False)
    except OSError as e:
        logging.warning("env: could not read %s (%s); relying on the shell", path, e)
