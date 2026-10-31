"""Login-gated rulebook PDFs (/rulebook/{filename})."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import rulebook
from app.core.auth import get_current_user


class Player:
    id = 2


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    (tmp_path / "eASLRB_v3_14_INHERIT_ZOOM.pdf").write_bytes(b"%PDF-1.4 rulebook")
    (tmp_path / "sections.json").write_text("{}")
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", tmp_path)
    monkeypatch.delenv("RULEBOOK_ACCEL_REDIRECT", raising=False)

    def make(user):
        app = FastAPI()
        app.include_router(rulebook.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)
    return make


def test_logged_out_fetch_is_401(make_client):
    r = make_client(None).get("/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf")
    assert r.status_code == 401


def test_logged_out_browser_goes_to_login(make_client):
    r = make_client(None).get("/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf",
                              headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_logged_in_gets_pdf(make_client):
    r = make_client(Player()).get("/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 rulebook"
    assert r.headers["content-type"] == "application/pdf"


def test_only_listed_files_are_served(make_client):
    client = make_client(Player())
    assert client.get("/rulebook/sections.json").status_code == 404
    assert client.get("/rulebook/..%2Fsecrets.pdf").status_code == 404


def test_accel_redirect_hands_off_to_nginx(make_client, monkeypatch):
    monkeypatch.setenv("RULEBOOK_ACCEL_REDIRECT", "/_rulebook/")
    r = make_client(Player()).get("/rulebook/ASL-QA-v31.pdf")
    assert r.status_code == 200
    assert r.headers["x-accel-redirect"] == "/_rulebook/ASL-QA-v31.pdf"
    assert r.content == b""
