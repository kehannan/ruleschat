# Production Environment Documentation

This document describes the production deployment of the ASL Rules Assistant at **kevmo.us**.

## 🌐 Production Environment

**URL**: https://kevmo.us  
**Server**: Digital Ocean Droplet  
**IP**: 143.110.153.155  
**OS**: Ubuntu 22.04 LTS  
**SSH Access**: `ssh mydigitalocean` (configured in `~/.ssh/config`)

## 📦 Architecture

```
Internet
    ↓
Cloudflare DNS (kevmo.us)
    ↓
Digital Ocean (143.110.153.155)
    ↓
Nginx (Port 443 - SSL/TLS)
    ↓
FastAPI Application (Port 8000)
    ↓
┌─────────────────┬─────────────────┬─────────────────┐
│   SQLite DB     │   OpenAI API    │   Vector Store  │
│  (mysite.db)    │  (gpt-4o model) │   (embeddings)  │
└─────────────────┴─────────────────┴─────────────────┘
```

## 🔧 Technology Stack

### Backend
- **Framework**: FastAPI 0.104.1
- **Web Server**: Uvicorn
- **Reverse Proxy**: Nginx
- **Database**: SQLite (mysite.db)
- **Authentication**: JWT (python-jose)
- **Password Hashing**: bcrypt (passlib)

### AI/ML
- **AI Provider**: OpenAI API
- **Model**: gpt-4o (configurable via DEFAULT_MODEL)
- **Vector Store**: OpenAI Embeddings
- **Knowledge Base**: ASL Rulebook (PDF → Vector Store)

### Frontend
- **Templates**: Jinja2
- **CSS Framework**: Bootstrap 5 (Bootswatch Flatly theme)
- **Real-time**: WebSockets for chat

## 📂 Production File Locations

```
/var/www/mysite2/               # Application root
├── .env                        # Environment variables (SECRET!)
├── mysite.db                   # SQLite database
├── responses_api_config.json   # Vector store configuration
├── app/                        # Main application
├── static/                     # Static assets
├── templates/                  # HTML templates
└── logs/                       # Application logs (if configured)
```

## 🔐 Environment Variables

Production `.env` file must include:

```bash
# OpenAI API
OPENAI_API_KEY=sk-...
OPENAI_ORG_ID=org-...
DEFAULT_MODEL=gpt-4o

# Security
SECRET_KEY=<random-64-char-hex>

# Admin
ADMIN_EMAIL=kevin.hannan@gmail.com

# Email (for invitations)
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
```

See `deployment/.env.example` for a template.

## 🚀 Deployment Process

### Initial Deployment

1. **Connect to server**:
   ```bash
   ssh mydigitalocean
   ```

2. **Clone repository**:
   ```bash
   cd /var/www
   git clone https://github.com/kehannan/mysite2.git
   cd mysite2
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**:
   ```bash
   cp deployment/.env.example .env
   nano .env  # Edit with production values
   ```

5. **Initialize database**:
   ```bash
   python scripts/init_db.py
   ```

6. **Setup vector store** (if needed):
   ```bash
   python scripts/setup_responses_api.py
   ```

7. **Configure nginx**:
   ```bash
   sudo cp deployment/nginx.conf /etc/nginx/sites-available/aslrules
   sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

8. **Setup systemd service**:
   ```bash
   sudo cp deployment/aslrules.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable aslrules
   sudo systemctl start aslrules
   ```

### Updating Production

```bash
# 1. Connect to server
ssh mydigitalocean

# 2. Navigate to app directory
cd /var/www/mysite2

# 3. Pull latest changes
git pull origin main

# 4. Install any new dependencies
pip install -r requirements.txt

# 5. Run any database migrations (if needed)
# python scripts/migrate_db.py

# 6. Restart the service
sudo systemctl restart aslrules

# 7. Check status
sudo systemctl status aslrules

# 8. Monitor logs
sudo journalctl -u aslrules -f
```

### Quick Restart

```bash
ssh mydigitalocean "sudo systemctl restart aslrules"
```

## 📊 Monitoring & Logs

### Application Logs
```bash
# View live logs
sudo journalctl -u aslrules -f

# View recent logs
sudo journalctl -u aslrules -n 100

# View logs from today
sudo journalctl -u aslrules --since today
```

### Nginx Logs
```bash
# Access log
sudo tail -f /var/log/nginx/access.log

# Error log
sudo tail -f /var/log/nginx/error.log
```

### Application Status
```bash
# Check if app is running
sudo systemctl status aslrules

# Check if nginx is running
sudo systemctl status nginx

# Check listening ports
sudo netstat -tulpn | grep LISTEN
```

## 🔒 Security Considerations

### SSL/TLS
- **Provider**: Let's Encrypt
- **Auto-renewal**: Configured via certbot
- **Certificate location**: `/etc/letsencrypt/live/kevmo.us/`

### Renewal
```bash
# Test renewal
sudo certbot renew --dry-run

# Force renewal (if needed)
sudo certbot renew --force-renewal
```

### Firewall
```bash
# Check UFW status
sudo ufw status

# Required ports
sudo ufw allow 22    # SSH
sudo ufw allow 80    # HTTP (for ACME challenges)
sudo ufw allow 443   # HTTPS
```

### API Keys
- **Never commit** `.env` to git
- Store in secure location (password manager)
- Rotate regularly
- Use read-only API keys where possible

## 🗄️ Database Management

### Backup
```bash
# Create backup
sqlite3 /var/www/mysite2/mysite.db ".backup '/var/www/mysite2/backups/mysite_$(date +%Y%m%d_%H%M%S).db'"

# Automated backup (add to crontab)
0 2 * * * sqlite3 /var/www/mysite2/mysite.db ".backup '/var/www/mysite2/backups/mysite_$(date +\%Y\%m\%d).db'"
```

### Restore
```bash
# Stop the application
sudo systemctl stop aslrules

# Restore from backup
cp /var/www/mysite2/backups/mysite_20250126.db /var/www/mysite2/mysite.db

# Start the application
sudo systemctl start aslrules
```

### Database Access
```bash
# Open SQLite CLI
sqlite3 /var/www/mysite2/mysite.db

# List tables
.tables

# Check users
SELECT email, api_key FROM users;
```

## 🧪 Testing Production

### Health Check
```bash
# Test HTTPS
curl -I https://kevmo.us

# Test API endpoint
curl https://kevmo.us/api/feedback -X POST -H "Content-Type: application/json" -d '{}'

# Test WebSocket (from browser console)
# ws = new WebSocket('wss://kevmo.us/ws/chat/')
```

### Load Testing
```bash
# Using Apache Bench
ab -n 100 -c 10 https://kevmo.us/

# Using wrk
wrk -t12 -c400 -d30s https://kevmo.us/
```

## 🐛 Troubleshooting

### App Won't Start
```bash
# Check service status
sudo systemctl status aslrules

# Check logs
sudo journalctl -u aslrules -n 50

# Check if port 8000 is in use
sudo lsof -i :8000

# Test manual start
cd /var/www/mysite2
python run.py
```

### 502 Bad Gateway
- App not running → `sudo systemctl start aslrules`
- Port mismatch → Check nginx config and app config
- Firewall blocking → Check UFW rules

### SSL Certificate Issues
```bash
# Check certificate expiry
sudo certbot certificates

# Renew manually
sudo certbot renew
```

### Database Locked
```bash
# Check for zombie processes
ps aux | grep python

# Kill if needed
sudo pkill -9 python

# Restart service
sudo systemctl restart aslrules
```

## 📈 Performance Optimization

### Current Configuration
- **Workers**: 1 (single Uvicorn process)
- **Database**: SQLite (sufficient for current load)
- **Caching**: None (consider Redis for scaling)

### Scaling Recommendations
When traffic increases:
1. **Add Gunicorn** with multiple Uvicorn workers
2. **Migrate to PostgreSQL** for better concurrent access
3. **Add Redis** for session storage and caching
4. **CDN** for static assets (Cloudflare)
5. **Load balancer** if multiple servers needed

## 🔗 Related Documentation

- [Deployment Guide](deployment/README.md) - Detailed deployment instructions
- [README.md](README.md) - Project overview and local development
- [TESTING.md](TESTING.md) - Testing guide
- [REFACTORING.md](REFACTORING.md) - Code refactoring notes

## 📞 Support

- **Repository**: https://github.com/kehannan/mysite2
- **Admin**: kevin.hannan@gmail.com
- **Production URL**: https://kevmo.us

