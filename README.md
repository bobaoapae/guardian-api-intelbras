# Intelbras Guardian API + Home Assistant Integration

Complete integration for controlling Intelbras Guardian alarm systems via Home Assistant.

[![Add Add-on Repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbobaoapae%2Fguardian-api-intelbras)

## Installation Options

### Option 1: Home Assistant Add-on (Recommended for Supervisor users)

If you're running Home Assistant OS or Supervised, you can install the API as an add-on:

1. Click the button above or manually add the repository:
   - **Settings** → **Add-ons** → **Add-on Store** → **⋮** (top right) → **Repositories**
   - Add: `https://github.com/bobaoapae/guardian-api-intelbras`

2. Find "**Intelbras Guardian API**" in the add-on store and click **Install**

3. Start the add-on and access the Web UI at `http://[YOUR_HA_IP]:8000`

4. Install the Home Assistant integration (see below)

### Option 2: Docker Compose (Standalone)

For Docker or Home Assistant Container users, see [Docker Deployment](#1-deploy-fastapi-middleware) below.

### Option 3: Manual Python

For development or custom setups, see [Development](#development) section.

---

## Architecture

This project implements a 3-layer architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                  HOME ASSISTANT (HACS Integration)              │
│                                                                 │
│  - Config Flow (manual host:port)                               │
│  - Coordinator (polling 30s)                                    │
│  - Entities:                                                    │
│    - alarm_control_panel (one per partition)                    │
│    - binary_sensor (one per zone)                               │
│    - sensor (last event)                                        │
│    - switch (eletrificador shock/alarm)                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP REST
┌───────────────────────────▼─────────────────────────────────────┐
│                  FASTAPI MIDDLEWARE (Container)                 │
│                                                                 │
│  - OAuth 2.0 authentication with Intelbras Cloud                │
│  - Automatic token refresh                                      │
│  - ISECNet Protocol (direct communication with alarm panel)     │
│  - State caching and management                                 │
│  - Zone friendly names management                               │
│  - Device password storage for auto-sync                        │
│  - Web UI for testing and management                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS + ISECNet
┌───────────────────────────▼─────────────────────────────────────┐
│  INTELBRAS INFRASTRUCTURE                                       │
│                                                                 │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐│
│  │  Cloud API          │    │  IP Receiver (Relay)            ││
│  │  api-guardian...    │    │  Forwards ISECNet commands      ││
│  │  :8443              │    │  to alarm panel                 ││
│  └──────────┬──────────┘    └─────────────┬───────────────────┘│
│             │                             │                     │
│             └──────────────┬──────────────┘                     │
│                            │                                    │
│  ┌─────────────────────────▼───────────────────────────────────┐│
│  │            ALARM PANEL (AMT, ANM, etc)                      ││
│  │                                                             ││
│  │  - Partitions (areas that can be armed independently)       ││
│  │  - Zones (sensors: doors, windows, motion, etc)             ││
│  │  - ISECNet Protocol V1/V2 for communication                 ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## How It Works

### Communication Flow

1. **Authentication**: User logs in with Intelbras account credentials. FastAPI obtains OAuth 2.0 tokens from Intelbras Cloud.

2. **Device Discovery**: FastAPI queries Intelbras Cloud API to list registered alarm panels (centrais) with their partitions and zones.

3. **Real-Time Status (ISECNet Protocol)**:
   - FastAPI connects to Intelbras IP Receiver
   - Sends ISECNet commands directly to the alarm panel
   - Receives real-time status: arm/disarm state, open zones, triggered alarms
   - This bypasses cloud latency for status updates

4. **Arm/Disarm Commands**:
   - Home Assistant sends command to FastAPI
   - FastAPI sends ISECNet command via IP Receiver to alarm panel
   - Panel executes command and returns result
   - Status is updated immediately

5. **Zone Monitoring**:
   - ISECNet provides real-time zone status (open/closed)
   - Friendly names can be assigned to zones via FastAPI
   - Binary sensors in Home Assistant reflect zone state

### ISECNet Protocol

ISECNet is Intelbras' proprietary protocol for direct communication with alarm panels:

- **Version 1**: Basic commands (arm, disarm, status)
- **Version 2**: Extended features (zone names, PGM control)

The protocol uses:
- TCP connection via Intelbras IP Receiver
- Binary packet format with CRC validation
- Password-based authentication per device
- Encryption for sensitive data

### Supported Devices

- **Alarm Panels**: AMT 2008, AMT 2010, AMT 2018, AMT 4010, ANM series
- **Electric Fences (Eletrificadores)**: ELC 5001, ELC 5002

## Features

### Alarm Control Panel
- Arm/disarm partitions
- Arm modes: Away (total) and Home (stay/perimeter)
- Triggered state detection
- Real-time status via ISECNet

### Zone Sensors (Binary Sensors)
- Real-time open/closed status
- Customizable friendly names
- Device class based on zone type (door, window, motion, smoke, etc.)
- Bypass status attribute

### Electric Fence Control (Switches)
- **Shock Switch**: Enable/disable electric shock
- **Alarm Switch**: Arm/disarm fence alarm

### Event Sensor
- Last event information
- Event history attributes

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Home Assistant 2023.x or later
- Intelbras Guardian alarm system with cloud access
- Intelbras account (email + password)
- Device password (programmed in alarm panel)

### 1. Deploy FastAPI Middleware

```bash
# Clone the repository
git clone https://github.com/bobaoapae/guardian-api-intelbras.git
cd guardian-api-intelbras

# Configure environment
cd fastapi_middleware
cp .env.example .env
# Edit .env and add your Home Assistant URL to CORS_ORIGINS

# Start the container
cd ../docker
docker-compose up -d

# Verify it's running
curl http://localhost:8000/api/v1/health
```

### 2. Access Web UI

Open http://localhost:8000 in your browser to:
- Test login with your Intelbras credentials
- View devices and their status
- Save device passwords for auto-sync
- Configure zone friendly names
- Test arm/disarm commands

### 3. Install Home Assistant Integration

```bash
# Copy integration to Home Assistant
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Restart Home Assistant
```

### 4. Configure Integration

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**
2. Search for "**Intelbras Guardian**"
3. Enter:
   - **Email**: Your Intelbras account email
   - **Password**: Your Intelbras account password
   - **FastAPI Host**: IP of the FastAPI container (e.g., 192.168.1.100)
   - **FastAPI Port**: 8000 (default)

### 5. Save Device Password

For real-time status via ISECNet:
1. Open FastAPI Web UI (http://localhost:8000)
2. Login with your Intelbras account
3. Click "Save Password" on your device
4. Enter the device password (configured in alarm panel)
5. Status will now sync automatically

## API Endpoints

### Authentication
- `POST /api/v1/auth/login` - Login with Intelbras credentials
- `POST /api/v1/auth/logout` - Logout and invalidate session
- `GET /api/v1/auth/session` - Get current session info

### Devices
- `GET /api/v1/devices` - List all alarm panels
- `GET /api/v1/devices/{id}` - Get device details

### Alarm Control
- `POST /api/v1/alarm/{device_id}/arm` - Arm partition
- `POST /api/v1/alarm/{device_id}/disarm` - Disarm partition
- `GET /api/v1/alarm/{device_id}/status` - Get real-time status (requires password)
- `GET /api/v1/alarm/{device_id}/status/auto` - Get status using saved password

### Password Management
- `POST /api/v1/devices/{device_id}/password` - Save device password
- `DELETE /api/v1/devices/{device_id}/password` - Delete saved password

### Zones
- `GET /api/v1/devices/{device_id}/zones` - Get zones with friendly names
- `PUT /api/v1/devices/{device_id}/zones/{zone_index}/friendly-name` - Set friendly name
- `DELETE /api/v1/devices/{device_id}/zones/{zone_index}/friendly-name` - Delete friendly name

### Events
- `GET /api/v1/events` - Get alarm event history

### Electric Fence (Eletrificador)
- `POST /api/v1/eletrificador/{device_id}/shock/on` - Enable shock
- `POST /api/v1/eletrificador/{device_id}/shock/off` - Disable shock
- `POST /api/v1/eletrificador/{device_id}/alarm/activate` - Arm alarm
- `POST /api/v1/eletrificador/{device_id}/alarm/deactivate` - Disarm alarm

## Project Structure

```
guardian-api-intelbras/
├── fastapi_middleware/           # FastAPI middleware
│   ├── app/
│   │   ├── main.py              # Application entry point
│   │   ├── core/                # Config, exceptions, security
│   │   ├── models/              # Pydantic models
│   │   ├── services/            # Business logic
│   │   │   ├── guardian_client.py    # Intelbras Cloud API client
│   │   │   ├── auth_service.py       # OAuth 2.0 authentication
│   │   │   ├── state_manager.py      # State/cache management
│   │   │   └── isecnet_protocol.py   # ISECNet implementation
│   │   ├── api/v1/              # REST endpoints
│   │   └── static/              # Web UI
│   └── tests/                   # Tests
├── docker/                      # Docker configuration
│   ├── Dockerfile
│   └── docker-compose.yml
├── home_assistant/              # Home Assistant integration
│   └── custom_components/
│       └── intelbras_guardian/
│           ├── __init__.py
│           ├── manifest.json
│           ├── config_flow.py
│           ├── coordinator.py
│           ├── api_client.py
│           ├── alarm_control_panel.py
│           ├── binary_sensor.py
│           ├── sensor.py
│           ├── switch.py
│           └── const.py
└── docs/                        # Documentation
```

## Environment Variables

### FastAPI (.env)

```env
# Intelbras API (do not change)
INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa

# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO

# CORS (add your Home Assistant URL)
CORS_ORIGINS=http://localhost:8123,http://homeassistant.local:8123

# Timeouts
HTTP_TIMEOUT=30
TOKEN_REFRESH_BUFFER=300
EVENT_POLL_INTERVAL=30
```

## Security Considerations

- **Credentials**: Never commit `.env` files with credentials
- **HTTPS**: Use a reverse proxy with SSL in production
- **CORS**: Restrict `CORS_ORIGINS` to trusted domains only
- **Device Passwords**: Stored encrypted in memory, not persisted to disk
- **Logging**: Sensitive data (passwords, tokens) are automatically filtered from logs

## Troubleshooting

### "Cannot connect to FastAPI middleware"
- Check if FastAPI container is running: `docker ps`
- Verify the host/port configuration
- Check firewall rules

### "Invalid credentials"
- Verify your Intelbras account email and password
- Try logging in at https://guardian.intelbras.com.br

### Arm/Disarm not working
- Ensure device password is saved correctly
- Check if device is online in Intelbras app
- Verify ISECNet connection in FastAPI logs

### Zones showing wrong status
- Save device password for real-time ISECNet status
- Check if zones are configured correctly in alarm panel

## Development

### Running FastAPI locally

```bash
cd fastapi_middleware
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Swagger UI

Access interactive API documentation at: http://localhost:8000/docs

## Roadmap

- [ ] Push notifications via Firebase Cloud Messaging
- [ ] PGM (programmable output) control
- [ ] Zone bypass functionality
- [ ] Local ISECNet support (without cloud relay)
- [ ] Google Assistant / Alexa integration

## Contributing

Contributions are welcome! Please:

1. Test with your Intelbras alarm system
2. Report issues with detailed logs
3. Submit pull requests with improvements

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Disclaimer

**THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.**

- This project is **NOT affiliated with, endorsed by, or associated with Intelbras** in any way
- Use of this software is **entirely at your own risk**
- The authors are **not responsible** for any damage, loss, or security issues that may result from using this software
- This software interacts with security systems - **improper use could compromise your security**
- Always ensure your alarm system is properly configured and tested
- Do not rely solely on this integration for security-critical applications

By using this software, you acknowledge that you understand and accept these terms.

## Support

- **Issues**: https://github.com/bobaoapae/guardian-api-intelbras/issues
- **Documentation**: Check the `/docs` folder

---

**Made for the Home Assistant community**
