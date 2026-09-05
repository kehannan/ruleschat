"""Per-user feature entitlements — section access on top of the admin/users groups.

Groups are exclusive (every user is in exactly one), so "may use the scenario
section" can't be a group without pulling people out of 'users'. An
entitlement is an extra grant a user holds alongside their group. Admins hold
every feature implicitly and need no rows.

Features are plain strings, enumerated in FEATURES so the admin page and the
invitation form can list them. Add a feature here first; nothing else needs a
schema change.
"""
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models import User, UserEntitlement
from app.services.user_service import is_admin

FEATURES = {
    "scenarios": "Scenario section — catalogue and chat",
    "scenarios.scans": "Scenario card scans (PDF view and export)",
}


def features_for(user: Optional[User]) -> set:
    """The feature names this user may use. Admins get all of them."""
    if not user:
        return set()
    if is_admin(user):
        return set(FEATURES)
    return {e.feature for e in (user.entitlements or [])}


def has(user: Optional[User], feature: str) -> bool:
    return feature in features_for(user)


def set_features(db: Session, user: User, features: Iterable[str],
                 granted_by: Optional[int] = None) -> set:
    """Make the user's entitlements exactly `features`. Unknown names are dropped.

    Returns the resulting set. Admins are left untouched — they hold every
    feature by group membership, and rows for them would only mislead.
    """
    if is_admin(user):
        return set(FEATURES)
    wanted = {f for f in features if f in FEATURES}
    current = {e.feature: e for e in user.entitlements}
    for name in set(current) - wanted:
        db.delete(current[name])
    for name in wanted - set(current):
        db.add(UserEntitlement(user_id=user.id, feature=name, granted_by=granted_by))
    db.commit()
    db.refresh(user)
    return wanted
