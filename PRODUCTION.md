# Production Environment Documentation

Production deployment of the ASL Rules Assistant at **kevmo.us**.

## Environment

**URL**: https://kevmo.us
**Server**: Digital Ocean Droplet (Ubuntu 22.04 LTS)
**IP**: 143.110.153.155
**SSH**: `ssh mydigitalocean` (configured in `~/.ssh/config`)

## Architecture

```
Internet → Cloudflare DNS (kevmo.us) → Digital Ocean (143.110.153.155)
  → Nginx (Port 443, SSL/TLS) → Uvicorn/FastAPI (Port 8000)
      → SQLite (mysite.db) + OpenAI API (gpt-5-mini / gpt-4.1-mini) + Vector Store (RAG)
```

## Technology Stack

- **Framework**: FastAPI
- **Web Server**: Uvicorn behind Nginx
- **Database**: SQLite
- **Auth**: JWT (python-jose) + bcrypt (passlib)
- **AI**: OpenAI Responses API with file_search (RAG, 20 chunks)
- **Models**: gpt-5-mini (default, higher accuracy), gpt-4.1-mini (faster, user-selectable)
- **Frontend**: Jinja2 templates, Bootstrap 5 (Bootswatch Flatly), WebSocket chat
- **SSL**: Let's Encrypt via certbot

## File Locations

```
/root/fastapi_app/mysite2/          # Application root
├── .env                             # Environment variables
├── mysite.db                        # SQLite database
├── responses_api_config.json        # Vector store configuration
├── data/evals/                      # Evaluation results
├── app/                             # Main application
├── static/                          # Static assets
└── templates/                       # HTML templates
```

## Environment Variables

Production `.env` must include:

```bash
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_ORG_ID=org-...
OPENAI_PROJECT_ID=proj-...
DEFAULT_MODEL=gpt-5-mini

# Security
SECRET_KEY=<random-64-char-hex>

# Admin
ADMIN_EMAIL=kevin.hannan@gmail.com

# Email (invitations)
MAIL_USERNAME=kevin.hannan@gmail.com
MAIL_PASSWORD=<app-specific-password>
MAIL_FROM=kevin.hannan@gmail.com
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_STARTTLS=True
MAIL_SSL_TLS=False

# Optional
TEMPERATURE=0.2
DATABASE_URL=sqlite:///./mysite.db
RAG_MAX_CHUNKS=20
COST_PER_1M_INPUT=0.25
COST_PER_1M_OUTPUT=1.00
```

## Deployment

### Updating Production

```bash
# From local machine
git push origin main

# On server
ssh mydigitalocean
cd /root/fastapi_app/mysite2
git pull origin main
pip install -r requirements.txt   # if dependencies changed
systemctl restart uvicorn
systemctl status uvicorn
```

### Quick Restart

```bash
ssh mydigitalocean "systemctl restart uvicorn"
```

## Monitoring

### Application Logs
```bash
sudo journalctl -u uvicorn -f          # Live logs
sudo journalctl -u uvicorn -n 100      # Recent logs
sudo journalctl -u uvicorn --since today
```

### Nginx Logs
```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Status Checks
```bash
systemctl status uvicorn
systemctl status nginx
sudo netstat -tulpn | grep LISTEN
```

## SSL

- **Provider**: Let's Encrypt
- **Auto-renewal**: certbot
- **Certificates**: `/etc/letsencrypt/live/kevmo.us/`

```bash
sudo certbot renew --dry-run      # Test renewal
sudo certbot renew --force-renewal # Force renewal
```

## Database

### Backup
```bash
sqlite3 /root/fastapi_app/mysite2/mysite.db ".backup '/root/fastapi_app/mysite2/backups/mysite_$(date +%Y%m%d_%H%M%S).db'"
```

### Access
```bash
sqlite3 /root/fastapi_app/mysite2/mysite.db
.tables
SELECT email FROM users;
.quit
```

## Troubleshooting

### App Won't Start
```bash
systemctl status uvicorn
sudo journalctl -u uvicorn -n 50
sudo lsof -i :8000
```

### 502 Bad Gateway
```bash
systemctl restart uvicorn
systemctl status nginx
```

### Database Locked
```bash
ps aux | grep python
systemctl restart uvicorn
```

## Related

- [README.md](README.md) — Project overview
- [deployment/QUICKSTART.md](deployment/QUICKSTART.md) — Quick deploy reference
- [TESTING.md](TESTING.md) — Testing guide
- **Admin**: kevin.hannan@gmail.com
- **Repository**: https://github.com/kehannan/mysite2
