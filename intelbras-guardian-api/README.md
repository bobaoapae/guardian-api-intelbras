# Intelbras Guardian API Add-on

API middleware for integrating Intelbras Guardian alarm systems with Home Assistant.

## About

This add-on provides a FastAPI middleware that communicates with Intelbras Guardian alarm panels using:
- **OAuth 2.0** authentication with Intelbras Cloud
- **ISECNet Protocol** for direct communication with alarm panels

## Features

- Real-time alarm status (arm/disarm, triggered, zones)
- Arm/Disarm partitions (away and home modes)
- Zone monitoring with custom friendly names
- Electric fence (eletrificador) control
- Event history
- Web UI for configuration and testing

## Installation

1. Add this repository to your Home Assistant Add-on Store:

   [![Add Repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbobaoapae%2Fguardian-api-intelbras)

   Or manually: **Settings** → **Add-ons** → **Add-on Store** → **⋮** → **Repositories** → Add `https://github.com/bobaoapae/guardian-api-intelbras`

2. Find "Intelbras Guardian API" in the add-on store and click **Install**

3. Start the add-on

4. Open the Web UI at `http://[YOUR_HA_IP]:8000`

## Configuration

```yaml
log_level: info
```

### Option: `log_level`

The log level for the add-on. Options: `trace`, `debug`, `info`, `warning`, `error`, `critical`

## Usage

### 1. Access Web UI

After starting the add-on, access `http://[YOUR_HA_IP]:8000` to:
- Login with your Intelbras account
- View your devices
- Save device passwords for ISECNet communication
- Configure zone friendly names
- Test arm/disarm commands

### 2. Install Home Assistant Integration

The add-on only provides the API middleware. You also need the Home Assistant integration:

1. Copy `home_assistant/custom_components/intelbras_guardian` to your Home Assistant `custom_components` folder

2. Restart Home Assistant

3. Add the integration: **Settings** → **Devices & Services** → **Add Integration** → "Intelbras Guardian"

4. Configure:
   - **Email**: Your Intelbras account email
   - **Password**: Your Intelbras account password
   - **FastAPI Host**: `localhost` or your HA IP
   - **FastAPI Port**: `8000`

### 3. Configure Device Password

For real-time status via ISECNet protocol, save your device password:

**Option A - Via Home Assistant:**
- Settings → Devices & Services → Intelbras Guardian → Configure → Configure Device Password

**Option B - Via Web UI:**
- Access `http://[YOUR_HA_IP]:8000` → Login → Click "Salvar Senha" on your device

## Network

The add-on exposes port `8000` for the API and Web UI.

## Support

- [GitHub Issues](https://github.com/bobaoapae/guardian-api-intelbras/issues)
- [Documentation](https://github.com/bobaoapae/guardian-api-intelbras)

## Disclaimer

This add-on is NOT affiliated with Intelbras. Use at your own risk.
