#!/usr/bin/env python3
"""
Smoke tests to run before deploying. Hits key routes and checks for
expected content / absence of known bad states.

Usage: python tests/smoke_test.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app
from starlette.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    results.append(condition)
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status} {name}{suffix}")
    return condition


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


# ---------------------------------------------------------------------------
section("Home page  GET /home")
r = client.get("/home")
check("200 status", r.status_code == 200, f"got {r.status_code}")
check("No 'Production Stats' section", "Production Stats" not in r.text)
check("No stat-card elements", "stat-card" not in r.text)
check("Hero heading present", "Advanced Squad Leader" in r.text)
check("How It Works section", "How It Works" in r.text)
check("Domain Challenge section", "Domain Challenge" in r.text)
check("No duplicate endblock", r.text.count("endblock") == 0)  # Jinja rendered, tags gone

# ---------------------------------------------------------------------------
section("Evals page  GET /evals")
r = client.get("/evals")
check("200 status", r.status_code == 200, f"got {r.status_code}")
check("gpt-5.4 row present", "gpt-5.4" in r.text)
check("gpt-5.4-mini row present", "gpt-5.4-mini" in r.text)
check("gpt-5-mini row present", "gpt-5-mini" in r.text)
check("gpt-4.1-mini row present", "gpt-4.1-mini" in r.text)
check("Estimated footnote (*)", "Estimated:" in r.text)
check("Key findings bullet 1", "best accuracy" in r.text)
check("Key findings bullet 2", "5x faster" in r.text)
check("Key findings bullet 3", "doesn't justify" in r.text)
check("Color classes present", "cell-good" in r.text)
check("No old findings cards", "finding-card" not in r.text)
check("No old runs table", "NEEDS REVIEW" not in r.text)

# ---------------------------------------------------------------------------
section("Usage API  GET /api/usage/daily")
r = client.get("/api/usage/daily")
check("200 status", r.status_code == 200, f"got {r.status_code}")
try:
    data = r.json()
    check("Has 'models' key", "models" in data)
    check("Has 'series' key", "series" in data)
    check("Has 'dates' key", "dates" in data)
    check("Models list is a list", isinstance(data.get("models"), list))
except Exception as e:
    check("Valid JSON", False, str(e))

# ---------------------------------------------------------------------------
section("Demo page  GET /demo")
r = client.get("/demo")
check("200 status", r.status_code == 200, f"got {r.status_code}")
check("No gpt-4.1-mini in demo dropdown", 'value="gpt-4.1-mini"' not in r.text)
check("gpt-5.4-mini in demo dropdown", 'value="gpt-5.4-mini"' in r.text)
check("gpt-5.4 in demo dropdown", 'value="gpt-5.4"' in r.text)

# ---------------------------------------------------------------------------
section("Ruleschat dropdown  GET /ruleschat")
r = client.get("/ruleschat", follow_redirects=False)
# Unauthenticated — expect redirect to login, not a 500
check("Not a 500", r.status_code != 500, f"got {r.status_code}")

# ---------------------------------------------------------------------------
print("\n" + "=" * 40)
passed = sum(results)
total = len(results)
print(f"{'✅ All' if passed == total else '❌'} {passed}/{total} checks passed")
print("=" * 40)
sys.exit(0 if passed == total else 1)
