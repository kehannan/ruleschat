"""Eval cases captured from the VASL extension (/api/ask/cases + admin pages)."""
import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ask as ask_module
from app.api import ask_cases
from app.core.auth import get_current_user

FIXTURE = Path(__file__).parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"


class _Group:
    name = "admin"


class Admin:
    id = 1
    api_key = "adminkey"
    group = _Group()


class Player:
    id = 2
    api_key = "playerkey"
    group = None


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(ask_cases, "CASES_DIR", tmp_path / "cases")
    keys = {"adminkey": Admin(), "playerkey": Player()}
    monkeypatch.setattr(ask_module, "_lookup_user_by_key", keys.get)
    app = FastAPI()
    app.include_router(ask_cases.router)
    app.dependency_overrides[get_current_user] = lambda: Admin()
    c = TestClient(app)
    c.cases_dir = tmp_path / "cases"
    return c


def _payload():
    vsav = ("data:application/octet-stream;base64,"
            + base64.b64encode(FIXTURE.read_bytes()).decode())
    return {
        "request": {"question": "H9 fires at H8 - IFT and DRM?", "vsav": vsav,
                    "game_phase": "PFPh",
                    "native_los": {"source": "57-H9", "target": "57-H8",
                                   "blocked": False, "hindrance": 0, "range": 1,
                                   "source_level": 0, "target_level": 0}},
        "answer": "The Finns attack on the 16 column at +2.",
        "note": "should be +2 foxhole",
        "extension_version": "0.7.0",
    }


def test_admin_key_saves_case_and_save(client):
    r = client.post("/api/ask/cases", json=_payload(),
                    headers={"Authorization": "Bearer adminkey"})
    assert r.status_code == 200, r.text
    case_id = r.json()["id"]
    d = client.cases_dir / case_id
    case = json.loads((d / "case.json").read_text())
    assert case["request"]["question"].startswith("H9 fires at H8")
    assert "vsav" not in case["request"]          # the save is stored as a file
    assert case["note"] == "should be +2 foxhole" and case["has_save"]
    assert (d / "save.vsav").read_bytes() == FIXTURE.read_bytes()


def test_non_admin_key_cannot_save(client):
    r = client.post("/api/ask/cases", json=_payload(),
                    headers={"Authorization": "Bearer playerkey"})
    assert r.status_code == 403
    assert not client.cases_dir.exists() or not any(client.cases_dir.iterdir())


def test_case_manifest_and_files(client):
    case_id = client.post("/api/ask/cases", json=_payload(),
                          headers={"Authorization": "Bearer adminkey"}).json()["id"]
    man = client.get(f"/api/admin/ask-cases/{case_id}/manifest").json()
    assert man["pieces"], "board manifest should list the counters"
    assert client.get(f"/api/admin/ask-cases/{case_id}/save.vsav").content == FIXTURE.read_bytes()
    assert client.get(f"/api/admin/ask-cases/{case_id}/../../etc").status_code == 404
    assert client.get("/api/admin/ask-cases/not-an-id/case.json").status_code == 404


def test_recompute_runs_current_calculator(client):
    case_id = client.post("/api/ask/cases", json=_payload(),
                          headers={"Authorization": "Bearer adminkey"}).json()["id"]
    case = ask_cases._load(case_id)
    result = ask_cases._recompute(case, ask_cases._case_dir(case_id))
    assert result["column"] == 16 and result["drm"] == 2, result
