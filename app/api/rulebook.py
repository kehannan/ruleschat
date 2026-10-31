"""Login-gated rulebook PDFs (eASLRB + ASL Q&A).

The PDFs are copyrighted, so they live outside static/ (in the gitignored
data/rulebook/) and are only handed to logged-in users. On prod nginx does
the actual file transfer (Range requests, sendfile): set
RULEBOOK_ACCEL_REDIRECT to the internal nginx location (e.g. "/_rulebook/")
and this route answers with an X-Accel-Redirect header instead of the bytes.
Locally it falls back to FileResponse.

static/rulebook/section_pages.json (section id -> page, no rule text) stays
public.
"""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.core.auth import get_current_user
from app.models.user import User

router = APIRouter()

RULEBOOK_DIR = Path(__file__).resolve().parents[2] / "data" / "rulebook"

# Only these files are served; anything else in data/rulebook/ (the extracted
# section text) never is.
PDF_FILES = {
    "eASLRB_v3_14_INHERIT_ZOOM.pdf",
    "ASL-QA-v31.pdf",
}


@router.get("/rulebook/{filename}", name="rulebook_pdf")
async def rulebook_pdf(filename: str, request: Request,
                       user: User = Depends(get_current_user)):
    if filename not in PDF_FILES:
        raise HTTPException(status_code=404)
    if not user:
        # A browser tab (the VASL extension's "Open full rulebook") goes to
        # the login page; pdf.js fetches get a plain 401.
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/login", status_code=303)
        raise HTTPException(status_code=401, detail="Log in to view the rulebook")

    headers = {"Cache-Control": "private, max-age=86400"}
    accel = os.getenv("RULEBOOK_ACCEL_REDIRECT")
    if accel:
        headers["X-Accel-Redirect"] = accel.rstrip("/") + "/" + filename
        return Response(headers=headers, media_type="application/pdf")

    path = RULEBOOK_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/pdf", headers=headers)
