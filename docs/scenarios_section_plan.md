# Scenarios as a ruleschat section — placement, permissions, integration

**Status:** phases 1 and 2 implemented · 2026-09-05 (phase 3 open)
**Goal:** make the scenario collection a first-class section of ruleschat that
invited non-admin users can be granted access to, with one login, one nav, and
one deploy — and decide whether it lives at `ruleschat.com/scenarios` or
`scenarios.ruleschat.com`.

---

## 1. Where it stands today

Everything below is already built and live; the plan changes as little of it
as possible.

| Piece | Today |
|---|---|
| Code | `app/api/scenarios.py` imports `agent`, `tools`, `serve`, `providers` from a sibling checkout at `SCENARIOS_DIR` (`/root/fastapi_app/scenarios` in prod). Unset → routes not registered. |
| Routes | `/scenarios` catalogue, `/scenarios/chat`, `/scenarios/api/{list,view,export}`, `/scenarios/ws` — **admin only**. `/scenarios-demo` (+ `/ws`) open, 5 questions/day/IP, in-memory. `/scenarios-design` open. `/experiments/scenarios` marketing page. |
| Access model | Two exclusive groups, `admin` and `users`; every user in exactly one. `_deny()` = not logged in → `/login`, not admin → `/`. The websocket handshake re-checks the same cookie. |
| Templates | The scenarios repo's own `base.html` with its own two-link nav. Inside ruleschat the page therefore shows **ruleschat's nav is absent** — it reads as a separate site. |
| Stylesheet | `/static/` is nginx-served from ruleschat's tree, so scenario pages use **ruleschat's copy** of `site-design-system.css`, which has already diverged from the scenarios repo's copy (modal styles) and needed a hand-mirrored rule this week. |
| Nav | Not in the top nav. Reachable only via Experiments → "Browse scenarios". |
| Hosting | One uvicorn on :8000, one nginx server block for `ruleschat.com`. `COOKIE_DOMAIN=.ruleschat.com` is already set "so scenarios.ruleschat.com shares this login", but that hostname has no DNS record and no server block. |
| Deploy | `deploy.sh` pulls only mysite2. The scenarios checkout is pulled by hand and gets forgotten (it was one commit behind today). |

Two things the user actually wants are missing: **(a)** a way to let a
non-admin person in, and **(b)** the section looking and navigating like part
of ruleschat.

---

## 2. Decision: path or subdomain

**Recommendation: stay on the path (`ruleschat.com/scenarios/…`), tidy it,
and don't build the subdomain.** Nothing in the permissions or integration
goals needs a separate host, and the subdomain adds four moving parts for a
cosmetic gain.

| | `/scenarios/…` (path) | `scenarios.ruleschat.com` (subdomain) |
|---|---|---|
| Login | Shared automatically; `url_for('login')` works. | Cookie is already domain-scoped, so shared — but every redirect (`/login?next=`) must become absolute and be validated against `*.ruleschat.com` to avoid an open redirect. |
| Routing | One router, done. | nginx second `server` block → same :8000; app must branch on `Host` (Starlette `Host` route or middleware) so `/` on the subdomain is the catalogue, not ruleschat home. |
| TLS / DNS | Nothing. | A record + `certbot --expand -d scenarios.ruleschat.com`; renewals now cover two names. |
| Static | Collides with ruleschat's `/static` (why the CSS is mirrored). Fix in §4 regardless. | Own `/static` namespace — the one real win. |
| Websockets | `/scenarios/ws` proxied by the existing `location /` (has the Upgrade headers). | Duplicate the ws proxy stanza in the new block. |
| Demo rate limit | Unchanged. | Unchanged. |
| Reversibility | — | Cheap to add later; expensive to remove once links exist. |

If a vanity hostname is wanted anyway, add an nginx block for
`scenarios.ruleschat.com` that **301s to `https://ruleschat.com/scenarios`**.
That gives the name without host-based routing in the app. Revisit a real
subdomain only if the section moves to its own process or box.

**Route tidy-up (part of this plan):** the three flat siblings become one
prefix, with 301s from the old paths.

| Old | New |
|---|---|
| `/scenarios-demo`, `/scenarios-demo/ws` | `/scenarios/demo`, `/scenarios/demo/ws` |
| `/scenarios-design` | `/scenarios/design` |
| `/experiments/scenarios` | unchanged (marketing copy stays under Experiments) |

---

## 3. Permissions: entitlements, not a third group

Groups are exclusive (`users.group_id`), so "scenarios" can't be a group
without taking people out of `users`. Model section access as an
**entitlement** a user holds in addition to their group.

### Schema

```sql
CREATE TABLE user_entitlements (
  user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  feature  TEXT    NOT NULL,          -- 'scenarios', 'scenarios.scans'
  granted_at DATETIME NOT NULL,
  granted_by INTEGER REFERENCES users(id),
  PRIMARY KEY (user_id, feature)
);
ALTER TABLE invitations ADD COLUMN entitlements TEXT;   -- JSON list, applied at registration
```

Two features, because the card scans are the publisher's artwork and deserve
a tighter gate than the metadata:

| Feature | Grants |
|---|---|
| `scenarios` | catalogue, chat with the full model picker, `/scenarios/api/list` |
| `scenarios.scans` | `/scenarios/api/view` and `/export` (the PDFs) |

`admin` implies every feature. Nothing else changes for existing users: the
`admin` account keeps working with no rows in the new table.

### Access matrix

| Who | Demo (capped, demo model) | Design page | Catalogue + chat | Card PDFs | Admin settings |
|---|---|---|---|---|---|
| Anonymous | ✓ | ✓ | → `/login?next=` | ✗ | ✗ |
| Logged in, no entitlement | ✓ (higher cap, e.g. 20/day, keyed by user not IP) | ✓ | "Ask Kevin for access" panel | ✗ | ✗ |
| `scenarios` | ✓ | ✓ | ✓ | ✗ (row shows "no scan access") | ✗ |
| `scenarios` + `scenarios.scans` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ |

### Code shape

- `app/services/entitlements.py`: `has(user, feature) -> bool` (admin
  short-circuits), `grant`, `revoke`, `list_for(user)`.
- `app/core/auth.py`: `require_entitlement(feature)` dependency factory returning
  the user or raising/redirecting exactly as `_deny` does now. **Both** the page
  routes and the `/scenarios/ws` handshake use it; the websocket keeps its
  explicit `get_current_user(websocket, db)` call and closes with 1008 as today.
- `app/api/scenarios.py`: replace `_deny(user)` with the dependency; `is_admin`
  stays only for the settings panel.
- Admin page: per-user checkboxes for the two features; "Send invitation" gains
  the same checkboxes, stored on the invitation and applied in `create_user`.
- Nav/template context: `get_base_context` adds `entitlements` (a set) so
  templates can do `{% if 'scenarios' in entitlements %}` and the nav script can
  honour `data-require-entitlement="scenarios"`.

Migration: additive only (one new table, one nullable column). Seed nothing.

---

## 4. Making it a section, not a guest

The section should render inside ruleschat's chrome and stop carrying its own
copy of the design system.

1. **Host base template.** Load templates through a `ChoiceLoader`: ruleschat's
   `templates/` first, then the scenarios repo's. The scenarios pages extend
   `base.html`, which now resolves to ruleschat's, and put their two-link nav in
   a section sub-nav block (the same pattern `experiments.html` uses for its
   subnav). The scenarios repo keeps a minimal `base.html` for standalone
   `run.py` use; nothing there changes for local development.
2. **Top nav.** Add `Scenarios` next to `Chat`, shown when the user holds
   `scenarios` (or is admin); anonymous visitors see it on the Experiments page
   and via the demo CTA, as now.
3. **One stylesheet.** Delete the scenarios repo's copy of
   `site-design-system.css` from the ruleschat-hosted path. Scenario-specific
   rules (the catalogue table, the card modal, `a.card-link`) move to a small
   `scenarios.css` served from the scenarios checkout at `/scenarios/static/`
   (a second `StaticFiles` mount; nginx's `/static/` alias doesn't catch it).
   Both hosts then load: design system from wherever they are, plus
   `scenarios.css`. The mirrored-rule chore disappears.
4. **Demo quota.** Move the in-memory per-IP counter onto the `DemoUsage`
   table ruleschat's own demo already uses, so restarts don't reset it and the
   admin page shows both demos in one place.
5. **Import hygiene (optional, later).** Replace the `sys.path.insert` with
   `pip install -e $SCENARIOS_DIR` and a proper package name, so `import agent`
   can't shadow anything in ruleschat. The DB and scans stay in the checkout as
   data; only the code becomes a package.

---

## 5. Deploy: one script, both checkouts

`deployment/deploy.sh` gains a step between pull and restart:

```bash
if [ -n "${SCENARIOS_DIR:-}" ] && [ -d "$SCENARIOS_DIR/.git" ]; then
  echo "==> Pulling scenarios checkout ($SCENARIOS_DIR) ..."
  git -C "$SCENARIOS_DIR" pull --ff-only origin main
fi
```

(`SCENARIOS_DIR` comes from `/root/fastapi_app/mysite2/.env`; source it or
pass it in.) The scans under `input/` are not in git and are synced by hand
when they change; `inventory/inventory.db` is in git, so a pull is a full data
deploy. Restart stays a single `systemctl restart uvicorn.service`.

---

## 6. Phases

| Phase | Scope | Size |
|---|---|---|
| 1 — Permissions | entitlements table + service + dependency, `scenarios.py` switched over, admin checkboxes, invitation carries entitlements, nav flag | ~1 day |
| 2 — Section | host base template + sub-nav, `Scenarios` in top nav, route tidy-up with 301s, `scenarios.css` + second static mount, demo quota on `DemoUsage`, `deploy.sh` pulls both | ~1 day |
| 3 — Optional | vanity `scenarios.ruleschat.com` → 301; package install instead of `sys.path` | hours |

Phase 1 is the one that unblocks inviting someone. Phase 2 is what makes it
feel like ruleschat. Neither needs the subdomain.

---

## 7. Open questions

- **Who gets `scenarios.scans`?** Default off for everyone but admin is the
  safe reading of "publisher's artwork"; decide whether trusted friends get it.
- **Demo cap for logged-in users** — 20/day keyed by user is a guess; pick a
  number.
- **Model picker for entitled users** — full list, or the demo model plus one
  paid option? Cost sits with the site owner either way.
