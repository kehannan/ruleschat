# Production Deployment Quick Start

This is a quick reference for deploying to production. For detailed documentation, see [PRODUCTION.md](../PRODUCTION.md).

## 📋 Pre-Deployment Checklist

- [ ] Server: Ubuntu 22.04 LTS
- [ ] Domain: DNS pointing to server IP
- [ ] SSH: Key-based authentication configured
- [ ] Firewall: Ports 22, 80, 443 open
- [ ] Dependencies: Python 3.10+, pip, nginx, certbot

## 🚀 Quick Deploy (Fresh Server)

```bash
# 1. Connect to server
ssh mydigitalocean

# 2. Install system dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip nginx certbot python3-certbot-nginx git -y

# 3. Clone repository
cd /var/www
sudo git clone https://github.com/kehannan/mysite2.git
cd mysite2

# 4. Install Python dependencies
sudo pip3 install -r requirements.txt

# 5. Configure environment
sudo cp deployment/env.example .env
sudo nano .env  # Fill in real values

# 6. Initialize database
sudo python3 scripts/init_db.py

# 7. Setup nginx
sudo cp deployment/nginx.conf /etc/nginx/sites-available/aslrules
sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# 8. Get SSL certificate
sudo certbot --nginx -d kevmo.us -d www.kevmo.us

# 9. Setup systemd service
sudo cp deployment/aslrules.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable aslrules
sudo systemctl start aslrules

# 10. Verify
sudo systemctl status aslrules
curl -I https://kevmo.us
```

## 🔄 Update Production

```bash
ssh mydigitalocean "cd /var/www/mysite2 && git pull && sudo systemctl restart aslrules"
```

## 📊 Common Commands

```bash
# View logs
sudo journalctl -u aslrules -f

# Restart app
sudo systemctl restart aslrules

# Check status
sudo systemctl status aslrules

# Reload nginx
sudo systemctl reload nginx

# Test nginx config
sudo nginx -t

# Renew SSL
sudo certbot renew --dry-run
```

## 🐛 Quick Fixes

### App not starting
```bash
sudo systemctl status aslrules
sudo journalctl -u aslrules -n 50
```

### 502 Bad Gateway
```bash
sudo systemctl restart aslrules
sudo systemctl status nginx
```

### Port already in use
```bash
sudo lsof -i :8000
sudo systemctl restart aslrules
```

## 📁 Important Files

| File | Location | Purpose |
|------|----------|---------|
| Environment | `/var/www/mysite2/.env` | API keys, secrets |
| Database | `/var/www/mysite2/mysite.db` | User data |
| Nginx Config | `/etc/nginx/sites-available/aslrules` | Reverse proxy |
| Service | `/etc/systemd/system/aslrules.service` | Systemd |
| SSL Certs | `/etc/letsencrypt/live/kevmo.us/` | TLS certificates |

## 🔗 Full Documentation

- [PRODUCTION.md](../PRODUCTION.md) - Complete production guide
- [README.md](../README.md) - Development setup
- [deployment/README.md](README.md) - Deployment details

