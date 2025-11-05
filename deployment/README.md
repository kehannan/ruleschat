# Deployment Configuration

This directory contains configuration files for deploying the ASL Rules Assistant to production servers.

## Files

### `nginx.conf`

Nginx reverse proxy configuration for the production server at `kevmo.us`.

**Features:**
- SSL/TLS termination with Let's Encrypt certificates
- HTTP to HTTPS redirect
- WebSocket support for real-time chat (`/ws/`)
- Proxy headers for FastAPI to detect real client IP and protocol
- ACME challenge support for certificate renewal

**Installation:**

1. Copy to nginx sites-available:
   ```bash
   sudo cp nginx.conf /etc/nginx/sites-available/aslrules
   ```

2. Enable the site:
   ```bash
   sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
   ```

3. Test configuration:
   ```bash
   sudo nginx -t
   ```

4. Reload nginx:
   ```bash
   sudo systemctl reload nginx
   ```

**Requirements:**
- Nginx installed on server
- Let's Encrypt SSL certificates for `kevmo.us`
- FastAPI app running on `localhost:8000`

## Production Deployment

### Server Setup

1. **Install dependencies:**
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip nginx certbot python3-certbot-nginx
   ```

2. **Clone repository:**
   ```bash
   cd /var/www
   git clone https://github.com/kehannan/mysite2.git
   cd mysite2
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   cp deployment/env.example .env
   # Edit .env with production values
   ```

5. **Setup database:**
   ```bash
   python scripts/init_db.py
   ```

6. **Configure nginx:**
   ```bash
   sudo cp deployment/nginx.conf /etc/nginx/sites-available/aslrules
   sudo ln -s /etc/nginx/sites-available/aslrules /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

7. **Setup SSL certificates:**
   ```bash
   sudo certbot --nginx -d kevmo.us -d www.kevmo.us
   ```

8. **Run application as service:**
   Create `/etc/systemd/system/aslrules.service`:
   ```ini
   [Unit]
   Description=ASL Rules Assistant
   After=network.target

   [Service]
   User=www-data
   WorkingDirectory=/var/www/mysite2
   Environment="PATH=/usr/local/bin:/usr/bin:/bin"
   ExecStart=/usr/bin/python3 run.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   Enable and start:
   ```bash
   sudo systemctl enable aslrules
   sudo systemctl start aslrules
   sudo systemctl status aslrules
   ```

## Monitoring

Check application logs:
```bash
sudo journalctl -u aslrules -f
```

Check nginx logs:
```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## Updating

To deploy updates:
```bash
cd /var/www/mysite2
git pull
sudo systemctl restart aslrules
```

