# Production Deployment Quick Start

Quick reference for deploying to production. For detailed docs, see [PRODUCTION.md](../PRODUCTION.md).

## Pre-Deployment Checklist

- [ ] Server: Ubuntu 22.04 LTS
- [ ] Domain: DNS pointing to server IP
- [ ] SSH: Key-based auth configured (`ssh mydigitalocean`)
- [ ] Firewall: Ports 22, 80, 443 open
- [ ] Dependencies: Python 3.10+, pip, nginx, certbot

## Quick Deploy (Fresh Server)

```bash
ssh mydigitalocean

# Install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip nginx certbot python3-certbot-nginx git -y

# Clone and setup
cd /root/fastapi_app
git clone https://github.com/kehannan/mysite2.git
cd mysite2
pip3 install -r requirements.txt

# Configure
cp deployment/env.example .env
nano .env  # Fill in real values

# Initialize
python3 scripts/init_db.py

# Nginx
sudo cp deployment/nginx.conf /etc/nginx/sites-available/aslrules
sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL
sudo certbot --nginx -d kevmo.us -d www.kevmo.us

# Service (uvicorn)
# Ensure uvicorn systemd service is configured
sudo systemctl enable uvicorn
sudo systemctl start uvicorn

# Verify
systemctl status uvicorn
curl -I https://kevmo.us
```

## Update Production

```bash
# Push from local
git push origin main

# Pull on server
ssh mydigitalocean
cd /root/fastapi_app/mysite2
git pull origin main
systemctl restart uvicorn
```

Or one-liner:
```bash
ssh mydigitalocean "cd /root/fastapi_app/mysite2 && git pull origin main && systemctl restart uvicorn"
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
| Environment | `/root/fastapi_app/mysite2/.env` | API keys, secrets |
| Database | `/root/fastapi_app/mysite2/mysite.db` | User data |
| Vector Store | `/root/fastapi_app/mysite2/responses_api_config.json` | RAG config |
| Eval Results | `/root/fastapi_app/mysite2/data/evals/` | Eval JSON files |
| Nginx Config | `/etc/nginx/sites-available/aslrules` | Reverse proxy |
| Service | systemd `uvicorn` | App process management |
| SSL Certs | `/etc/letsencrypt/live/kevmo.us/` | TLS certificates |

## Full Documentation

- [PRODUCTION.md](../PRODUCTION.md) — Complete production guide
- [README.md](../README.md) — Project overview
