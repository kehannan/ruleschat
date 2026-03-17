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

## Update Production

```bash
# Push from local
git push origin main

# Pull and restart on server
ssh your-server "cd /your/app/directory && git pull origin main && systemctl restart uvicorn"
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
