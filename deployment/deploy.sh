#!/usr/bin/env bash
#
# Production deploy for the ruleschat FastAPI app.
#
# Run ON the server:
#   cd /root/fastapi_app/mysite2 && ./deployment/deploy.sh
#
# Pulls the latest main, reinstalls deps only if requirements.txt changed,
# reloads nginx, restarts the app, and verifies both services are active.
set -euo pipefail

APP_DIR="/root/fastapi_app/mysite2"
SERVICE="uvicorn.service"
BRANCH="main"
APP_URL="http://localhost:8000/evals"   # local health check (the app's port)

cd "$APP_DIR"

echo "==> [1/4] Pulling latest origin/$BRANCH ..."
before=$(git rev-parse HEAD)
git pull origin "$BRANCH"
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then
  echo "    already up to date ($after)"
else
  echo "    $before -> $after"
fi

# Reinstall dependencies only when requirements.txt actually changed in this pull.
if git diff --name-only "$before" "$after" | grep -qx 'requirements.txt'; then
  echo "==> [2/4] requirements.txt changed — installing deps ..."
  pip install -r requirements.txt
else
  echo "==> [2/4] requirements.txt unchanged — skipping pip install"
fi

echo "==> [3/4] Reloading nginx + restarting $SERVICE ..."
systemctl reload nginx
systemctl restart "$SERVICE"

echo "==> [4/4] Service status (expect: active / active):"
systemctl is-active nginx "$SERVICE"

echo "==> Health check $APP_URL (waiting for the app to come up)..."
code="000"
for _ in $(seq 1 10); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$APP_URL" || true)
  # Any real HTTP response (200, 3xx redirect, 401, ...) means the app is up.
  # Only "000" means no connection yet — wait and retry.
  [ -n "$code" ] && [ "$code" != "000" ] && break
  sleep 2
done
echo "    HTTP $code"
if [ -z "$code" ] || [ "$code" = "000" ]; then
  echo "!!! App not responding after ~20s — check: journalctl -u $SERVICE -n 50"
  exit 1
fi
echo "==> Deploy OK (app responding, HTTP $code)."
