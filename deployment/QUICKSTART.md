# Production Deployment Quick Start

Quick reference for deploying to a Ubuntu 22.04 LTS server behind nginx.

## Pre-Deployment Checklist

- [ ] Server: Ubuntu 22.04 LTS
- [ ] Domain: DNS pointing to server IP
- [ ] SSH: Key-based auth configured
- [ ] Firewall: Ports 22, 80, 443 open
- [ ] Dependencies: Python 3.10+, pip, nginx, certbot

## Quick Deploy (Fresh Server)

```bash
# Install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip nginx certbot python3-certbot-nginx git -y

# Clone and setup
cd /your/app/directory
git clone https://github.com/your-username/asl-rules-assistant.git
cd asl-rules-assistant
pip3 install -r requirements.txt

# Configure
cp deployment/env.example .env
nano .env  # Fill in real values

# Initialize database and create admin user
python3 scripts/init_db.py

# Configure and enable nginx
sudo cp deployment/nginx.conf /etc/nginx/sites-available/aslrules
# Edit nginx.conf to replace YOUR_DOMAIN with your actual domain
sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL
sudo certbot --nginx -d your-domain.com -d www.your-domain.com

# Service (uvicorn)
sudo systemctl enable uvicorn
sudo systemctl start uvicorn

# Verify
systemctl status uvicorn
curl -I https://your-domain.com
```

## Update Production (deploy latest `main`)

There is no CI/CD — deploying is: get `main` pushed, then on the server pull and
restart the app.

```bash
# 1. From local: make sure main is pushed
git push origin main

# 2. On the server: pull and restart
ssh <your-server> "cd /var/www/mysite2 && git pull origin main && sudo systemctl restart <service>"
```

- **App directory:** `/var/www/mysite2` (the systemd unit's `WorkingDirectory`).
- **Service name:** confirm which unit is enabled before restarting —
  `ssh <your-server> "systemctl list-units --type=service | grep -iE 'uvicorn|aslrules'"`
  (the unit file is `aslrules.service`; older notes say `uvicorn`).
- **Most changes are pull + restart only** (content, templates, eval JSON under
  `data/evals/`). Extra steps only when:
  - `requirements.txt` changed → add `&& pip3 install -r requirements.txt` before the restart.
  - DB schema changed → run the relevant migration / `python3 scripts/init_db.py` step.

### Verify
```bash
curl -I https://<your-domain>/evals          # expect 200 (or 302 to login)
# eyeball: /evals (one gpt-5.4 row + "Not yet." answer) and /evals/v1.0 (6-model archive)
```

### If it doesn't come back up
```bash
ssh <your-server> "sudo journalctl -u <service> -n 50"   # last 50 log lines
ssh <your-server> "sudo systemctl restart <service>"      # a 502 is usually a stale restart
```

## Common Commands

```bash
# Logs
sudo journalctl -u uvicorn -f

# Restart
systemctl restart uvicorn

# Status
systemctl status uvicorn

# Nginx
sudo nginx -t && sudo systemctl reload nginx

# SSL renewal
sudo certbot renew --dry-run
```

## Quick Fixes

### App not starting
```bash
systemctl status uvicorn
sudo journalctl -u uvicorn -n 50
```

### 502 Bad Gateway
```bash
systemctl restart uvicorn
systemctl status nginx
```

### Port in use
```bash
sudo lsof -i :8000
systemctl restart uvicorn
```

## Important Files

| File | Location | Purpose |
|------|----------|---------|
| Environment | `<app-dir>/.env` | API keys, secrets |
| Database | `<app-dir>/mysite.db` | User data |
| Vector Store Config | `<app-dir>/responses_api_config.json` | RAG config (copy from example) |
| Eval Results | `<app-dir>/data/evals/` | Eval JSON files |
| Nginx Config | `/etc/nginx/sites-available/aslrules` | Reverse proxy |
| Service | systemd `uvicorn` | App process management |
| SSL Certs | `/etc/letsencrypt/live/your-domain.com/` | TLS certificates |
