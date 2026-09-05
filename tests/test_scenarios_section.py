"""The scenarios section mounted under ruleschat: routes, redirects, chrome.

Needs the scenarios checkout: set SCENARIOS_DIR, or have it as a sibling
directory (../scenarios). Skipped otherwise. Runs in a subprocess because
app.main registers the section's routes only if SCENARIOS_DIR is set at
import time, and another test may already have imported it without.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKOUT = os.getenv("SCENARIOS_DIR") or str(ROOT.parent / "scenarios")

SCRIPT = r"""
import json, os, sys
os.environ["SCENARIOS_DIR"] = sys.argv[1]
os.environ.pop("AGENTIC_DEBUG_LOG", None)
from fastapi.testclient import TestClient
from app.main import app
from app.core.auth import get_current_user
c = TestClient(app)
def get(url, **kw):
    try:
        return c.get(url, follow_redirects=False, **kw)
    except TypeError:
        return c.get(url, allow_redirects=False, **kw)
out = {}
r = get("/scenarios");            out["catalogue_anon"] = [r.status_code, r.headers.get("location")]
r = get("/scenarios-demo");       out["old_demo"] = [r.status_code, r.headers.get("location")]
r = get("/scenarios-design");     out["old_design"] = [r.status_code, r.headers.get("location")]
r = get("/scenarios/demo")
subnav = r.text[r.text.find("section-subnav"):]; subnav = subnav[:subnav.find("</nav>")]
out["demo"] = [r.status_code, "section-subnav" in r.text,
               "ASL <em>Scenarios</em>" in r.text and "Ruleschat" not in r.text,
               "/scenarios/static/css/scenarios.css" in r.text,
               'href="/scenarios/demo"' in subnav, 'href="/scenarios"' in subnav]
r = get("/scenarios/design");     out["design"] = [r.status_code, "section-subnav" in r.text]
r = get("/scenarios/static/css/scenarios.css"); out["css"] = [r.status_code, ".section-subnav" in r.text]
r = get("/scenarios/static/../../inventory/inventory.db"); out["traversal"] = r.status_code

# An entitled viewer (admin holds every feature): the private pages render in
# ruleschat's chrome with the Catalogue/Chat sub-nav.
from app.models import Group, User
app.dependency_overrides[get_current_user] = lambda: User(email="root@example.com", group=Group(name="admin"))
r = get("/scenarios")
subnav = r.text[r.text.find("section-subnav"):]; subnav = subnav[:subnav.find("</nav>")]
out["catalogue_admin"] = [r.status_code, "Ruleschat" in r.text, 'href="/scenarios"' in subnav,
                          'href="/scenarios/chat"' in subnav, 'href="/scenarios/demo"' in subnav]
r = get("/scenarios/chat"); out["chat_admin"] = [r.status_code, "chat-toolbar" in r.text, "/scenarios/ws" in r.text]
print(json.dumps(out))
"""


@pytest.mark.skipif(not (Path(CHECKOUT) / "webapp" / "main.py").exists(),
                    reason="scenarios checkout not available")
def test_section_under_ruleschat():
    proc = subprocess.run([sys.executable, "-c", SCRIPT, CHECKOUT], cwd=ROOT,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["catalogue_anon"] == [303, "/login"]
    assert out["old_demo"] == [301, "/scenarios/demo"]
    assert out["old_design"] == [301, "/scenarios/design"]
    status, subnav, own_nav, css, demo_link, catalogue_link = out["demo"]
    assert status == 200 and subnav and css
    assert not own_nav, "page rendered the standalone chrome, not ruleschat's"
    assert demo_link and not catalogue_link, "visitor sub-nav should offer the demo, not the catalogue"
    assert out["design"] == [200, True]
    assert out["css"] == [200, True]
    assert out["traversal"] == 404
    assert out["catalogue_admin"] == [200, True, True, True, False]
    assert out["chat_admin"] == [200, True, True]
