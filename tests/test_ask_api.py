"""Tests for POST /api/ask (the VASL-extension machine API).

The router is mounted on a bare FastAPI app so app.main's import-time DB
work never runs; DB lookup and the LLM service are stubbed at the module
seams (_lookup_user_by_key, get_asl_service).
"""
import base64
import json
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
        timing = {"input_tokens": 1200, "output_tokens": 300}
        if stream:
            gen = iter([{"status": "searching"}, "THE ", "ANSWER"])
            # Mirror the real service: the OpenAI streaming paths return a
            # (generator, timing_data) tuple; iterating the tuple itself
            # would hand the endpoint a generator object as its first delta.
            return (gen, timing) if kwargs.get("return_timing") else (gen, [])
        if kwargs.get("return_timing"):
            return "THE ANSWER", timing
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


def test_vsav_account_key_honors_selected_agentic_registry_model(client):
    r = client.post(
        "/api/ask",
        json={"question": "Resolve the CC in this hex",
              "vsav": _vsav_data_url(), "model": "ox-alpha"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "z-ai/glm-5.3-flash"
    call = client.fake_service.calls[-1]
    assert call["model"] == "z-ai/glm-5.3-flash"
    assert call["use_agentic"] is True


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
    # tokens + list-price cost ride on the done line
    assert lines[-1]["tokens_in"] == 1200 and lines[-1]["tokens_out"] == 300
    assert lines[-1]["cost_usd"] is not None and lines[-1]["cost_usd"] > 0


def test_non_stream_reports_tokens_and_cost(client):
    r = client.post("/api/ask", json={"question": "hi"},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tokens_in"] == 1200 and j["tokens_out"] == 300
    assert isinstance(j["cost_usd"], float) and j["cost_usd"] > 0


def test_unknown_model_has_null_cost(client):
    # OpenRouter pass-through with an arbitrary slug: tokens yes, price unknown
    r = client.post("/api/ask", json={"question": "hi", "model": "vendor/unpriced-x"},
                    headers={"Authorization": "Bearer sk-or-abc"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tokens_in"] == 1200 and j["cost_usd"] is None


def test_provider_reported_cost_overrides_registry_estimate():
    usage = ask_module._usage_fields(
        "z-ai/glm-5.3-flash",
        {"input_tokens": 1200, "output_tokens": 300,
         "provider_cost_usd": 0.0123456},
    )
    assert usage["cost_usd"] == 0.012346


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


def test_history_is_capped_for_vector_search(client):
    long_history = [["q" * 1200, "a" * 1200] for _ in range(6)]
    r = client.post(
        "/api/ask",
        json={"question": "Current?", "history": long_history},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200
    q = client.fake_service.calls[-1]["question"]
    assert len(q) <= ask_module.ASK_MAX_RETRIEVAL_QUERY_CHARS
    assert q.endswith("Current question: Current?")


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


def test_render_native_los_line():
    clear = ask_module._render_native_los({
        "source": "57-A3", "target": "57-A6", "blocked": False,
        "hindrance": 1, "range": 3, "source_level": 0, "target_level": 1})
    assert clear == ("VASL NATIVE LOS: 57-A3 (ground level of hex) -> 57-A6 "
                     "(1 level(s) above the hex's ground): "
                     "clear, hindrance DRM +1, range 3 "
                     "[hex elevations are in the BOARD STATE terrain annotations]")
    blocked = ask_module._render_native_los({
        "source": "A3", "target": "A6", "blocked": True,
        "reason": "Woods", "range": 3})
    assert "BLOCKED (Woods)" in blocked
    assert ask_module._render_native_los(None) is None


# --- subscription CLI models ---------------------------------------------------

class _AdminGroup:
    name = "admin"


@pytest.fixture
def admin_client(client, monkeypatch):
    class AdminUser(FakeUser):
        group = _AdminGroup()
    monkeypatch.setattr(
        ask_module, "_lookup_user_by_key",
        lambda key: AdminUser() if key == FakeUser.api_key else None)
    return client


@pytest.mark.parametrize("key,slug", [
    ("claude-fable-sub", "claude-cli/claude-fable-5-1"),
    ("claude-opus-sub", "claude-cli/claude-opus-5-5"),
    ("gpt-6-astra-sub", "codex-cli/gpt-6-astra"),
])
def test_admin_subscription_model_with_vsav_runs_agentic(admin_client, key, slug):
    r = admin_client.post(
        "/api/ask",
        json={"question": "Resolve this fire attack",
              "vsav": _vsav_data_url(), "model": key},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == slug
    call = admin_client.fake_service.calls[-1]
    assert call["model"] == slug
    assert call["use_agentic"] is True   # tools reach the CLI via the MCP bridge
    assert "BOARD STATE" in call["board_state"]


def test_admin_subscription_model_streams_without_vsav(admin_client):
    r = admin_client.post(
        "/api/ask/stream", json={"question": "hi", "model": "claude-fable-sub"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    done = json.loads(r.text.strip().splitlines()[-1])
    assert done["model"] == "claude-cli/claude-fable-5-1"
    assert admin_client.fake_service.calls[-1]["use_agentic"] is False


def test_non_admin_cannot_pick_subscription_model(client):
    r = client.post(
        "/api/ask",
        json={"question": "hi", "vsav": _vsav_data_url(),
              "model": "claude-fable-sub"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == ask_module.VSAV_MODEL
    assert client.fake_service.calls[-1]["use_agentic"] is True


@pytest.mark.parametrize("slug", ["claude-cli/claude-fable-5-1",
                                  "codex-cli/gpt-6-astra"])
def test_provider_mode_rejects_subscription_slugs(client, slug):
    r = client.post("/api/ask", json={"question": "hi", "model": slug},
                    headers={"Authorization": "Bearer sk-or-callerkey"})
    assert r.status_code == 400
    assert client.fake_service.calls == []


def test_stream_heartbeats_and_tool_status_during_blocking_call(client, monkeypatch):
    """Subscription CLI calls block for the whole tool loop; the stream must
    keep sending lines (nginx drops silent upstreams after 60s)."""
    import time as _time
    monkeypatch.setattr(ask_module, "STREAM_HEARTBEAT_SECONDS", 0.05)
    orig = client.fake_service.get_answer

    def slow_answer(question, stream=False, **kwargs):
        kwargs["on_tool_call"]("resolve_attack", {"firing_hex": "57-H9"})
        _time.sleep(0.3)
        return orig(question, stream=stream, **kwargs)
    client.fake_service.get_answer = slow_answer

    r = client.post("/api/ask/stream", json={"question": "hi"},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    lines = [json.loads(l) for l in r.text.strip().splitlines()]
    statuses = [l["status"] for l in lines if "status" in l]
    assert statuses[0] == "Resolving the attack from the save file"
    assert len(statuses) >= 3  # tool label + heartbeats repeating it
    assert "".join(l.get("delta", "") for l in lines) == "THE ANSWER"
    assert lines[-1]["done"] is True


# --- server-side precomputed attack -------------------------------------------

_HAZMO_LOS = {"source": "57-H9", "target": "57-H8", "blocked": False,
              "hindrance": 0, "range": 1, "source_level": 0, "target_level": 0}


def test_fire_question_precomputes_attack_and_prepends_summary(client):
    r = client.post(
        "/api/ask",
        json={"question": "H9 Prep Fires at H8 - what is the IFT and DRM?",
              "vsav": _vsav_data_url(), "native_los": _HAZMO_LOS,
              "game_phase": "PFPh"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    answer = r.json()["answer"]
    summary, _, rest = answer.partition("\n\n---\n\n")
    assert summary.startswith("**Calculator** · 57-H9 → 57-H8, Prep Fire")
    assert "| IFT column | **16** |" in summary and "| Final DRM | **+2** |" in summary
    assert rest == "THE ANSWER"
    # the model gets the full result, marked authoritative
    board = client.fake_service.calls[-1]["board_state"]
    assert "PRECOMPUTED ATTACK RESOLUTION" in board and '"total_fp": 16' in board


def test_phase_named_in_question_beats_the_phase_wheel(client):
    r = client.post(
        "/api/ask",
        json={"question": "H9 fires on H8 in Advance Fire Phase - IFT and DRM?",
              "vsav": _vsav_data_url(), "native_los": _HAZMO_LOS,
              "game_phase": "PFPh"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"].startswith(
        "**Calculator** · 57-H9 → 57-H8, Advancing Fire")


@pytest.mark.parametrize("question,phase", [
    ("S3 fires at V4 in the AFPh", "advancing"),
    ("prep fire S3 at V4", "prep"),
    ("Defensive First Fire from S3 at V4", "defensive_first"),
    ("S3 Final Fire at V4", "defensive_final"),
    ("S3 fires at V4 in the Defensive Fire Phase", "defensive_final"),
    ("S3 fires at V4", None),
])
def test_phase_from_question(question, phase):
    assert ask_module._phase_from_question(question) == phase


def test_stream_sends_calculator_summary_first(client):
    r = client.post(
        "/api/ask/stream",
        json={"question": "Resolve H9 firing at H8", "vsav": _vsav_data_url(),
              "native_los": _HAZMO_LOS, "game_phase": "PFPh"},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    lines = [json.loads(l) for l in r.text.strip().splitlines()]
    assert lines[0]["delta"].startswith("**Calculator** · 57-H9 → 57-H8")


def test_los_only_question_is_not_precomputed(client):
    r = client.post(
        "/api/ask",
        json={"question": "Can H9 see H8?", "vsav": _vsav_data_url(),
              "native_los": _HAZMO_LOS},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.json()["answer"] == "THE ANSWER"
    assert "PRECOMPUTED" not in client.fake_service.calls[-1]["board_state"]


def test_rich_client_gets_calculator_html_not_markdown(client):
    r = client.post(
        "/api/ask/stream",
        json={"question": "Resolve H9 firing at H8", "vsav": _vsav_data_url(),
              "native_los": _HAZMO_LOS, "game_phase": "PFPh", "rich": True},
        headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    lines = [json.loads(l) for l in r.text.strip().splitlines()]
    html = lines[0]["calculator_html"]
    assert "57-H9 → 57-H8 · Prep Fire · range 1 (PBF)" in html
    assert "<div class='tileval'>16</div>" in html and "+2" in html
    assert "Broken" in html and "bgcolor" in html
    # the build: per-firer FP worksheet, then the DRM grouped by side
    assert "FIREPOWER" in html and "Printed FP" in html and "×2 = 12" in html
    assert "DICE ROLL MODIFIERS" in html and "Target" in html and "Total DRM" in html
    # the answer text carries only the model's explanation
    assert "".join(l.get("delta", "") for l in lines) == "THE ANSWER"
    assert "ANSWER FORMAT" in client.fake_service.calls[-1]["extra_instructions"]


def test_calculator_html_escapes_and_reports_blocked_los():
    html = ask_module._calculator_html(
        {"error": "LOS is blocked <Orchard> & co"},
        {"source": "41-S8", "target": "41-W5"})
    assert "no attack" in html and "&lt;Orchard&gt; &amp; co" in html


# --- rule text for the citation popup -----------------------------------------

@pytest.fixture
def rules_fixture():
    from app.asl import rules_lookup
    fixtures = Path(__file__).resolve().parent / "fixtures"
    rules_lookup.reset()
    rules_lookup.load_sections(str(fixtures / "rulebook_sections_fixture.json"),
                               str(fixtures / "qa_entries_fixture.json"))
    yield
    rules_lookup.reset()


def test_rule_text_for_account_key(client, rules_fixture):
    r = client.get("/api/ask/rule/Z1.11",
                   headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["section"] == "Z1.11" and body["page"] == 10
    assert body["text"].startswith("Z1.11 DOUBLE ACTIVATION")


def test_rule_text_falls_back_to_parent(client, rules_fixture):
    r = client.get("/api/ask/rule/Z1.119",
                   headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 200 and r.json()["section"] == "Z1.11"
    assert "not found" in r.json()["note"]


def test_rule_text_refuses_provider_keys_and_unknown_sections(client, rules_fixture):
    r = client.get("/api/ask/rule/Z1.11",
                   headers={"Authorization": "Bearer sk-or-v1-abc"})
    assert r.status_code == 403
    r = client.get("/api/ask/rule/Q99",
                   headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.status_code == 404


def test_rule_text_reflows_pdf_line_breaks(client, monkeypatch):
    from app.asl import rules_lookup
    monkeypatch.setattr(rules_lookup, "get_section", lambda s, include_qa: {
        "section": "B9.31", "page": 119,
        "text": "9.31 The wall/hedge TEM is not cumulative with oth-\ner terrain\nin that hex."})
    r = client.get("/api/ask/rule/B9.31",
                   headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert r.json()["text"] == ("9.31 The wall/hedge TEM is not cumulative "
                                "with other terrain in that hex.")


def test_precompute_skips_ordnance_only_no_attack():
    # A Gun in the firing hex is excluded from the IFT attack; when nothing
    # else fires, the plugin must not show an IFT "no attack" card above the
    # ordnance_attack answer.
    from app.api.ask import _ordnance_only
    from app.asl.attack.steps.firers import ORDNANCE_EXCLUSION
    gun_only = {"error": "All candidate firers in 46-Z1 were excluded (['ordnance (Gun) — "
                         f"{ORDNANCE_EXCLUSION}, NOT the IFT; excluded from this attack'])."}
    assert _ordnance_only(gun_only)
    assert not _ordnance_only({"error": "LOS blocked at 46-W2"})
    assert not _ordnance_only({"total_fp": 4, "column": 4})
    assert not _ordnance_only(None)


# --- TH/TK calculator card from an ordnance_attack result ---------------------

def _ordnance_result():
    from app.asl import thtk
    return thtk.compute_attack(gun="76LL", nationality="British", range_hexes=6,
                               target={"kind": "armored", "hull_af": 18, "turret_af": 14},
                               target_moving="moving", acquired=-1, target_size=-1)


def _run_hook(kwargs, name="ordnance_attack", result=None):
    hook = kwargs.get("on_tool_result")
    assert hook, "endpoint must pass an on_tool_result hook"
    hook(name, {}, result if result is not None else _ordnance_result())


@pytest.mark.parametrize("during_stream", [False, True])
def test_stream_sends_thtk_card_when_model_calls_ordnance_attack(client, during_stream):
    svc = client.fake_service
    real = svc.get_answer

    def get_answer(question, stream=False, **kwargs):
        if not during_stream:          # CLI path: tools run before the stream
            _run_hook(kwargs)
            return real(question, stream=stream, **kwargs)
        gen, timing = real(question, stream=stream, **kwargs)

        def gen2():                    # API path: tools run while streaming
            _run_hook(kwargs)
            yield from gen
        return gen2(), timing
    svc.get_answer = get_answer

    r = client.post("/api/ask/stream", json={"question": "TH/TK?", "rich": True},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    lines = [json.loads(l) for l in r.text.strip().splitlines()]
    cards = [l["calculator_html"] for l in lines if "calculator_html" in l]
    assert len(cards) == 1
    html = cards[0]
    assert "76LL AP" in html and "TO HIT" in html and "TO KILL" in html
    assert "Target DRM · C6" in html and "Final TK#" in html and "Hit on Original DR" in html
    assert "".join(l.get("delta", "") for l in lines) == "THE ANSWER"


def test_plain_client_and_errors_get_no_thtk_card(client):
    svc = client.fake_service
    real = svc.get_answer

    def get_answer(question, stream=False, **kwargs):
        _run_hook(kwargs, result={"error": "ambiguous"})
        _run_hook(kwargs, name="ift_attack", result={"column": 4})
        return real(question, stream=stream, **kwargs)
    svc.get_answer = get_answer
    r = client.post("/api/ask/stream", json={"question": "q", "rich": True},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert "calculator_html" not in r.text
    svc.get_answer = lambda question, stream=False, **kw: (_run_hook(kw), real(question, stream, **kw))[1]
    r = client.post("/api/ask", json={"question": "q"},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert "calculator_html" not in r.json()          # not a rich client
    r = client.post("/api/ask", json={"question": "q", "rich": True},
                    headers={"Authorization": f"Bearer {FakeUser.api_key}"})
    assert "TO KILL" in r.json()["calculator_html"]


def test_execute_tool_reports_results_to_hook():
    from app.asl.tools import execute_tool
    seen = []
    r = execute_tool("thtk_odds", {"modified_th": 9},
                     context={"on_tool_result": lambda n, a, res: seen.append((n, res))})
    assert seen == [("thtk_odds", r)]
    # a failing hook never breaks the tool call
    assert execute_tool("thtk_odds", {"modified_th": 9},
                        context={"on_tool_result": lambda *a: 1 / 0})["p_hit"] > 0


def test_only_first_ordnance_call_is_carded_and_facing_is_labelled():
    from app.asl import thtk
    shot = lambda facing: thtk.compute_attack(
        gun="76LL", nationality="British", range_hexes=6,
        target={"kind": "armored", "facing": facing, "hull_af": 18 if facing == "front" else 6,
                "turret_af": 14 if facing == "front" else 6})
    cards = []
    hook = ask_module._ordnance_card_hook(cards.append)
    hook("ordnance_attack", {}, {"error": "bad gun"})       # failed tries don't count
    hook("ordnance_attack", {}, shot("front"))
    hook("ordnance_attack", {}, shot("side"))                # the model's what-if
    assert len(cards) == 1
    assert "(front)" in cards[0] and "Target front armor (AF)" in cards[0]
    assert "-18" in cards[0] and "(side)" not in cards[0]


def test_board_less_vasl_los_dropped_on_multi_board_map():
    two = {"boards": [{"name": "r43"}, {"name": "46"}]}
    bare = {"source": "T8", "target": "W7", "hindrance": 2}
    assert ask_module._native_los_board_unknown(bare, two)
    assert not ask_module._native_los_board_unknown({**bare, "board": "46"}, two)
    assert not ask_module._native_los_board_unknown(
        {**bare, "source": "46-T8", "target": "46-W7"}, two)
    assert not ask_module._native_los_board_unknown(bare, {"boards": [{"name": "46"}]})
    assert not ask_module._native_los_board_unknown(None, two)
