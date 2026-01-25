# Deployment Guide

Complete instructions for deploying the Intelbras Guardian integration.

## Prerequisites

- Docker and Docker Compose installed
- Home Assistant 2023.x or later
- Network access to Intelbras cloud services
- Intelbras Guardian account (email + password)
- Alarm panel device password

## Architecture Overview

```
┌────────────────────┐     ┌────────────────────┐     ┌──────────────────┐
│   Home Assistant   │────▶│  FastAPI Container │────▶│  Intelbras Cloud │
│   (your server)    │     │  (Docker)          │     │  + IP Receiver   │
└────────────────────┘     └────────────────────┘     └──────────────────┘
```

The FastAPI middleware container can run:
- On the same machine as Home Assistant
- On a separate server in your network
- On a VPS/cloud server (not recommended for latency)

## Option 1: Docker Compose (Recommended)

### Step 1: Clone Repository

```bash
git clone https://github.com/bobaoapae/guardian-api-intelbras.git
cd guardian-api-intelbras
```

### Step 2: Configure Environment

```bash
cd fastapi_middleware
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO

# CORS - Add your Home Assistant URL
CORS_ORIGINS=http://localhost:8123,http://192.168.1.100:8123,http://homeassistant.local:8123

# Intelbras API (do not change these)
INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa
```

### Step 3: Start Container

```bash
cd ../docker
docker-compose up -d
```

### Step 4: Verify Installation

```bash
# Check container is running
docker ps

# Check health endpoint
curl http://localhost:8000/api/v1/health

# Check logs
docker logs intelbras-guardian-api
```

### Step 5: Access Web UI

Open http://localhost:8000 in your browser to:
- Test login with your credentials
- Verify devices are listed
- Save device passwords
- Test arm/disarm functionality

## Option 2: Manual Docker

```bash
# Build image
cd fastapi_middleware
docker build -t intelbras-guardian-api -f ../docker/Dockerfile .

# Run container
docker run -d \
  --name intelbras-guardian-api \
  -p 8000:8000 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e CORS_ORIGINS="http://localhost:8123,http://homeassistant.local:8123" \
  --restart unless-stopped \
  intelbras-guardian-api
```

## Option 3: Direct Python (Development)

```bash
cd fastapi_middleware

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env as needed

# Run server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Home Assistant Integration Setup

### Step 1: Copy Integration Files

```bash
# If Home Assistant is on the same machine
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Or via SSH/SCP
scp -r home_assistant/custom_components/intelbras_guardian \
      user@homeassistant:/config/custom_components/
```

### Step 2: Restart Home Assistant

Go to **Settings** → **System** → **Restart**

Or via CLI:
```bash
ha core restart
```

### Step 3: Add Integration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "**Intelbras Guardian**"
4. Enter configuration:
   - **Email**: Your Intelbras account email
   - **Password**: Your Intelbras account password
   - **FastAPI Host**: IP address of FastAPI container
   - **FastAPI Port**: 8000 (default)

### Step 4: Verify Entities

After setup, you should see:
- `alarm_control_panel.intelbras_guardian_*` - One per partition
- `binary_sensor.intelbras_guardian_*` - One per zone
- `sensor.intelbras_guardian_last_event` - Event sensor
- `switch.intelbras_guardian_*` - For eletrificadores (if applicable)

## Production Considerations

### HTTPS with Reverse Proxy (nginx)

For production, use nginx as a reverse proxy with SSL:

```nginx
server {
    listen 443 ssl;
    server_name guardian-api.yourdomain.com;

    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Systemd Service (without Docker)

Create `/etc/systemd/system/intelbras-guardian.service`:

```ini
[Unit]
Description=Intelbras Guardian API
After=network.target

[Service]
Type=simple
User=guardian
WorkingDirectory=/opt/intelbras-guardian/fastapi_middleware
Environment=PATH=/opt/intelbras-guardian/venv/bin
ExecStart=/opt/intelbras-guardian/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable intelbras-guardian
sudo systemctl start intelbras-guardian
```

### Firewall Configuration

Open only the necessary port:

```bash
# UFW (Ubuntu)
sudo ufw allow from 192.168.1.0/24 to any port 8000

# firewalld (CentOS/Fedora)
sudo firewall-cmd --zone=internal --add-port=8000/tcp --permanent
sudo firewall-cmd --reload
```

### Resource Limits (Docker)

Add to `docker-compose.yml`:

```yaml
services:
  fastapi:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
        reservations:
          cpus: '0.1'
          memory: 128M
```

## Updating

### Docker Compose

```bash
cd guardian-api-intelbras
git pull
cd docker
docker-compose build
docker-compose up -d
```

### Home Assistant Integration

```bash
# Remove old files
rm -rf /config/custom_components/intelbras_guardian

# Copy new files
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Restart Home Assistant
```

## Troubleshooting

### Container won't start

Check logs:
```bash
docker logs intelbras-guardian-api
```

Common issues:
- Port 8000 already in use
- Invalid environment variables
- Network connectivity problems

### Cannot connect from Home Assistant

1. Verify container is running: `docker ps`
2. Check container IP: `docker inspect intelbras-guardian-api | grep IPAddress`
3. Test from HA machine: `curl http://container-ip:8000/api/v1/health`
4. Check firewall rules

### Authentication fails

1. Verify credentials work at https://guardian.intelbras.com.br
2. Check FastAPI logs for detailed error
3. Ensure Intelbras cloud services are accessible

### Arm/Disarm not working

1. Verify device password is correct (same as in official app)
2. Check ISECNet connection in logs
3. Ensure device is online and connected

## Backup and Recovery

### Backup

Important data to backup:
- `.env` file (contains configuration)
- Home Assistant config entry (automatically stored)

Note: Device passwords and zone friendly names are stored in memory and will be lost on container restart. Re-save them via Web UI after restart.

### Recovery

1. Restore `.env` file
2. Start container
3. Re-authenticate in Home Assistant
4. Re-save device passwords via Web UI

## Monitoring

### Health Check

The container includes a health check. Monitor with:

```bash
docker inspect --format='{{.State.Health.Status}}' intelbras-guardian-api
```

### Logs

View real-time logs:
```bash
docker logs -f intelbras-guardian-api
```

### Metrics (Optional)

For production monitoring, consider adding:
- Prometheus metrics endpoint
- Grafana dashboards
- Alert rules for failures
