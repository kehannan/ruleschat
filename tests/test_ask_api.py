"""Tests for POST /api/ask (the VASL-extension machine API).

The router is mounted on a bare FastAPI app so app.main's import-time DB
work never runs; DB lookup and the LLM service are stubbed at the module
seams (_lookup_user_by_key, get_asl_service).
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ask as ask_module

FIXTURE = Path(__file__).parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"


class FakeUser:
    id = 7
    api_key = "accountkey123"
    group = None  # is_admin(user) -> False


class FakeService:
    """Captures get_answer kwargs; copy.copy-able like the real singleton."""
    def __init__(self):
        self.calls = []
        self.openrouter_client = "ENV_CLIENT"

    def get_answer(self, question, stream=False, **kwargs):
        self.calls.append({"question": question, "stream": stream, **kwargs,
                           "openrouter_client": self.openrouter_client})
        if stream:
            gen = iter([{"status": "searching"}, "THE ", "ANSWER"])
            # Mirror the real service: the OpenAI streaming paths return a
            # (generator, timing_data) tuple; iterating the tuple itself
            # would hand the endpoint a generator object as its first delta.
            return (gen, {}) if kwargs.get("return_timing") else (gen, [])
        return "THE ANSWER"


@pytest.fixture
def client(monkeypatch):
    fake_service = FakeService()
    monkeypatch.setattr(ask_module, "get_asl_service", lambda: fake_service)
    monkeypatch.setattr(
        ask_module, "_lookup_user_by_key",
        lambda key: FakeUser() if key == FakeUser.api_key else None)
    monkeypatch.setattr(ask_module, "_usage", {"day": None, "counts": {}})
    app = FastAPI()
    app.include_router(ask_module.router)
    c = TestClient(app)
    c.fake_service = fake_service
    return c


def _vsav_data_url() -> str:
    raw = FIXTURE.read_bytes()
    return ("data:application/octet-stream;base64,"
            + base64.b64encode(raw).decode())


# --- auth -----------------------------------------------------------------

def test_missing_auth_is_401(client):
    r = client.post("/api/ask", json={"question": "hi"})
    assert r.status_code == 401


def test_unknown_key_is_401(client):
    r = client.post("/api/ask", json={"question": "hi"},
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_account_key_answers(client):
    r = client.post("/api/ask", json={"question": "What is a squad?"},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "THE ANSWER"
    assert body["mode"] == "account"
    assert body["remaining_today"] == ask_module.ASK_DAILY_LIMIT - 1
    # default chat model, no vsav -> no agentic, env openrouter client
    call = client.fake_service.calls[-1]
    assert call["use_agentic"] is False
    assert call["board_state"] is None


def test_provider_key_swaps_openrouter_client(client, monkeypatch):
    captured = {}

    class FakeORClient:
        def __init__(self, api_key, app_name=None, app_url=None):
            captured["api_key"] = api_key
    monkeypatch.setattr(ask_module, "OpenRouterClient", FakeORClient)

    r = client.post(
        "/api/ask",
        json={"question": "hi", "model": "moonshotai/kimi-k3"},
        headers={"Authorization": "Bearer sk-or-v1-abc123"})
    assert r.status_code == 200
    assert r.json()["mode"] == "provider"
    assert r.json()["model"] == "moonshotai/kimi-k3"
    assert captured["api_key"] == "sk-or-v1-abc123"
    # request-scoped copy got the caller's client; the singleton kept its own
    call = client.fake_service.calls[-1]
    assert isinstance(call["openrouter_client"], FakeORClient)
    assert client.fake_service.openrouter_client == "ENV_CLIENT"


def test_provider_mode_rejects_non_openrouter_model(client):
    r = client.post("/api/ask", json={"question": "hi", "model": "gpt-5.4"},
                    headers={"Authorization": "Bearer sk-or-v1-abc"})
    assert r.status_code == 400


def test_daily_limit(client, monkeypatch):
    monkeypatch.setattr(ask_module, "ASK_DAILY_LIMIT", 2)
    hdr = {"Authorization": f"Bearer {FakeUser.api_key}"}
    assert client.post("/api/ask", json={"question": "a"}, headers=hdr).status_code == 200
    assert client.post("/api/ask", json={"question": "b"}, headers=hdr).status_code == 200
    assert client.post("/api/ask", json={"question": "c"}, headers=hdr).status_code == 429


# --- vsav / fog of war ------------------------------------------------------

def test_vsav_flow_with_side(client):
    r = client.post(
        "/api/ask",
        json={"question": "How many Russian squads?",
              "vsav": _vsav_data_url(), "side": "Russian"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["board"]["perspective"] == "Russian"
    assert body["board"]["validation"] == "71/71"
    assert body["model"] == ask_module.VSAV_MODEL  # forced for tools
    call = client.fake_service.calls[-1]
    assert call["use_agentic"] is True
    assert "Perspective: Russian" in call["board_state"]
    assert call["vsav_state"]["validation"]["n_matched"] == 71


def test_vsav_player_maps_to_side(client):
    # "finn_player" is in the fixture's player_sides -> Finnish
    r = client.post(
        "/api/ask",
        json={"question": "q", "vsav": _vsav_data_url(),
              "side": "Axis", "player": "finn_player"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    # "Axis" matches no unit side; the player id resolves instead
    assert r.json()["board"]["perspective"] == "Finnish"


def test_bad_vsav_is_400(client):
    r = client.post(
        "/api/ask",
        json={"question": "q",
              "vsav": "data:application/octet-stream;base64,AAAA"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 400


# --- streaming + history ------------------------------------------------------

def test_stream_endpoint_ndjson(client):
    import json as _json
    r = client.post("/api/ask/stream", json={"question": "hi"},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    lines = [_json.loads(l) for l in r.text.strip().split("\n")]
    assert lines[0] == {"status": "searching"}
    assert [l.get("delta") for l in lines[1:3]] == ["THE ", "ANSWER"]
    assert lines[-1]["done"] is True
    assert lines[-1]["mode"] == "account"
    assert "remaining_today" in lines[-1]
    # the endpoint must request timing and unwrap the (generator, timing) tuple
    assert client.fake_service.calls[-1]["return_timing"] is True
    assert not any("generator" in (l.get("delta") or "") for l in lines)


def test_stream_auth_still_http_error(client):
    r = client.post("/api/ask/stream", json={"question": "hi"},
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_history_prefixes_question(client):
    r = client.post(
        "/api/ask",
        json={"question": "And with a leader?",
              "history": [["What is the TEM of woods?", "+1 (B13.2)."]]},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    q = client.fake_service.calls[-1]["question"]
    assert q.startswith("Earlier exchanges")
    assert "What is the TEM of woods?" in q
    assert q.endswith("Current question: And with a leader?")


def test_no_history_leaves_question_untouched(client):
    client.post("/api/ask", json={"question": "plain"},
                headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert client.fake_service.calls[-1]["question"] == "plain"


# --- perspective resolution unit tests ---------------------------------------

def test_resolve_perspective_fail_closed():
    state = {"hexes": {"h": {"units": [{"side": "Russian"}]}},
             "offboard": [], "player_sides": {"p1": "Russian"}}
    # exact side match wins
    assert ask_module._resolve_perspective(state, "Russian", None) == "Russian"
    # unknown side + known player -> mapped
    assert ask_module._resolve_perspective(state, "Allied", "p1") == "Russian"
    # unresolvable side passes through (masks everything: fail closed)
    assert ask_module._resolve_perspective(state, "Axis", "nobody") == "Axis"
    # nothing given -> full view
    assert ask_module._resolve_perspective(state, None, None) is None
