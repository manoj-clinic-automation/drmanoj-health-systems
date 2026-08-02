# GutLog — personal health logger

Single-file Flask app + SQLite. One user, password-protected, tap-first mobile UI.
Tabs: **Log** (daily symptoms) · **Meds** (PRN) · **Food** (trigger trials) · **Vitals** · **Review** (30-day chart, tables, CSV export).

- First visit asks you to **create a password** (min 8 chars). Everything after that is locked behind it.
- Symptom log is **one row per day** — saving again the same day updates it.
- All data lives in one file: `health.db` (plus `health.db.secret` for the session key). **Back these up.**

---

## Quick local test (your laptop)

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
GUTLOG_INSECURE=1 venv/bin/python app.py     # http://127.0.0.1:8000
```
(`GUTLOG_INSECURE=1` only relaxes the secure-cookie flag for plain-HTTP testing. Never set it on the VPS.)

---

## VPS deployment (Ubuntu/Debian, nginx, HTTPS)

Assumes your domain (e.g. `log.yourdomain.in`) already points at the VPS (an A record).

### 1. App

```bash
sudo mkdir -p /opt/gutlog && sudo chown $USER /opt/gutlog
# copy app.py + requirements.txt into /opt/gutlog, then:
cd /opt/gutlog
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. systemd service

`sudo nano /etc/systemd/system/gutlog.service`:

```ini
[Unit]
Description=GutLog health logger
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/gutlog
ExecStart=/opt/gutlog/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /opt/gutlog
sudo systemctl daemon-reload
sudo systemctl enable --now gutlog
sudo systemctl status gutlog      # should be active (running)
```

### 3. nginx reverse proxy

`sudo nano /etc/nginx/sites-available/gutlog`:

```nginx
server {
    listen 80;
    server_name log.yourdomain.in;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/gutlog /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 4. HTTPS (mandatory — this is health data)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d log.yourdomain.in     # choose "redirect HTTP to HTTPS"
```

Certbot auto-renews. Done — open `https://log.yourdomain.in` on your phone, set the password, and use your browser's **Add to Home Screen** so it opens like an app.

### 5. Backups (one line, weekly)

```bash
sudo crontab -e
# add:
0 3 * * 0 cp /opt/gutlog/health.db /opt/gutlog/backup_$(date +\%F).db
```
Occasionally download a copy off the server too (`scp`), and CSV exports from the Review tab are a second safety net.

---

## Hardening notes (optional but sensible)

- **Extra lock**: since it's a single user, you can add nginx basic-auth in front as a second factor
  (`sudo apt install apache2-utils; htpasswd -c /etc/nginx/.htpasswd manoj;` then add
  `auth_basic "GutLog"; auth_basic_user_file /etc/nginx/.htpasswd;` inside the `location /` block).
- **Or skip the public internet entirely**: install Tailscale on the VPS and your phone and bind gunicorn to the tailnet IP — then nothing is exposed publicly at all.
- The app already: hashes the password (PBKDF2), locks out for 60 s after 5 wrong attempts, uses HTTPOnly/Secure/SameSite cookies, and caps note lengths.
- To **reset a forgotten password**: `sqlite3 /opt/gutlog/health.db "DELETE FROM settings WHERE key='pw_hash';"` then reload the site — it will ask you to set a new one. (Data is untouched.)

## Environment variables (all optional)

| Var | Purpose |
|---|---|
| `GUTLOG_DB` | Path to the SQLite file (default: `health.db` beside `app.py`) |
| `GUTLOG_SECRET` | Fixed session secret; otherwise one is generated and stored in `health.db.secret` |
| `GUTLOG_INSECURE=1` | Allow cookies over plain HTTP — local testing only |
