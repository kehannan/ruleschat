"""Entitlements: the service rules and the require_entitlement dependency.

Runs against an in-memory SQLite so nothing touches mysite.db.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.auth import get_current_user, require_entitlement
from app.database import Base
from app.models import Group, User
from app.services import entitlements as ent


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin_g, users_g = Group(name="admin"), Group(name="users")
    session.add_all([admin_g, users_g])
    session.flush()
    session.add_all([
        User(email="root@example.com", group_id=admin_g.id),
        User(email="pat@example.com", group_id=users_g.id),
    ])
    session.commit()
    yield session
    session.close()


def _user(db, email):
    return db.query(User).filter(User.email == email).one()


def test_admin_holds_every_feature_without_rows(db):
    root = _user(db, "root@example.com")
    assert ent.features_for(root) == set(ent.FEATURES)
    assert root.entitlements == []


def test_plain_user_starts_with_nothing(db):
    assert ent.features_for(_user(db, "pat@example.com")) == set()
    assert ent.features_for(None) == set()


def test_set_features_grants_revokes_and_drops_unknown(db):
    pat = _user(db, "pat@example.com")
    assert ent.set_features(db, pat, ["scenarios", "bogus"]) == {"scenarios"}
    assert ent.has(pat, "scenarios") and not ent.has(pat, "scenarios.scans")

    ent.set_features(db, pat, ["scenarios", "scenarios.scans"])
    assert ent.features_for(pat) == {"scenarios", "scenarios.scans"}

    ent.set_features(db, pat, [])
    assert ent.features_for(pat) == set()


def test_set_features_leaves_admin_alone(db):
    root = _user(db, "root@example.com")
    assert ent.set_features(db, root, ["scenarios"]) == set(ent.FEATURES)
    assert root.entitlements == []


def test_entitlements_load_with_the_user(db):
    """Users are handed out from sessions that close immediately, so the
    grants must come along eagerly rather than lazy-load later."""
    pat = _user(db, "pat@example.com")
    ent.set_features(db, pat, ["scenarios"])
    db.expunge_all()
    pat = _user(db, "pat@example.com")
    db.close()
    assert ent.has(pat, "scenarios")


def _client(user):
    app = FastAPI()
    page = require_entitlement("scenarios", no_access_url="/scenarios-demo")
    api = require_entitlement("scenarios.scans", api=True)

    @app.get("/page")
    def page_route(u: User = Depends(page)):
        return {"ok": u.email}

    @app.get("/api")
    def api_route(u: User = Depends(api)):
        return {"ok": u.email}

    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _get(client, url):
    """No redirect-following, on both the httpx- and requests-based TestClient."""
    try:
        return client.get(url, follow_redirects=False)
    except TypeError:
        return client.get(url, allow_redirects=False)


def test_dependency_anonymous(db):
    c = _client(None)
    r = _get(c, "/page")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _get(c, "/api").status_code == 401


def test_dependency_signed_in_without_grant(db):
    c = _client(_user(db, "pat@example.com"))
    r = _get(c, "/page")
    assert r.status_code == 303 and r.headers["location"] == "/scenarios-demo"
    assert _get(c, "/api").status_code == 403


def test_dependency_with_grant_and_admin(db):
    pat = _user(db, "pat@example.com")
    ent.set_features(db, pat, ["scenarios"])
    c = _client(pat)
    assert _get(c, "/page").status_code == 200
    assert _get(c, "/api").status_code == 403       # scans not granted

    ent.set_features(db, pat, ["scenarios", "scenarios.scans"])
    assert _get(_client(pat), "/api").status_code == 200
    assert _get(_client(_user(db, "root@example.com")), "/api").status_code == 200
