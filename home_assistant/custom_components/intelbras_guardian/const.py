"""Constants for Intelbras Guardian integration."""

DOMAIN = "intelbras_guardian"

# Configuration keys
CONF_FASTAPI_HOST = "fastapi_host"
CONF_FASTAPI_PORT = "fastapi_port"
CONF_SESSION_ID = "session_id"
CONF_DEVICE_PASSWORD = "device_password"

# Default values
DEFAULT_FASTAPI_PORT = 8000
DEFAULT_SCAN_INTERVAL = 5

# Eletrificador models
ELETRIFICADOR_MODELS = ["ELC", "ELETRIFICADOR"]

# State mapping: API -> Home Assistant (Alarm)
# The API now returns arm_mode values from ISECNet protocol
STATE_MAPPING = {
    # ISECNet states (from our implementation)
    "armed_away": "armed_away",
    "armed_stay": "armed_home",
    "disarmed": "disarmed",
    # Legacy/fallback states
    "ARMED": "armed_away",
    "ARMED_AWAY": "armed_away",
    "ARMED_STAY": "armed_home",
    "ARMED_HOME": "armed_home",
    "DISARMED": "disarmed",
    # Eletrificador states
    "ACTIVATED": "armed_away",
    "DEACTIVATED": "disarmed",
    "PARTIAL": "armed_home",
    "STAY": "armed_home",
}

# Reverse mapping: Home Assistant -> API
REVERSE_STATE_MAPPING = {
    "armed_away": "away",
    "armed_home": "home",
    "disarmed": "disarmed",
}

# Eletrificador state mapping
ELETRIFICADOR_STATE_MAPPING = {
    "ACTIVATED": True,
    "ON": True,
    "ARMED": True,
    "DEACTIVATED": False,
    "OFF": False,
    "DISARMED": False,
    True: True,
    False: False,
}

# Zone type to device class mapping
ZONE_TYPE_DEVICE_CLASS = {
    "door": "door",
    "window": "window",
    "motion": "motion",
    "smoke": "smoke",
    "gas": "gas",
    "glass_break": "vibration",
    "panic": "safety",
    "generic": "opening",
}

# Platforms
PLATFORMS = ["alarm_control_panel", "binary_sensor", "button", "sensor", "switch"]

# Events
EVENT_ALARM = f"{DOMAIN}_alarm_event"

# Unified alarm configuration
CONF_UNIFIED_ALARM = "unified_alarm"  # Enable unified alarm entity
CONF_HOME_PARTITIONS = "home_partitions"  # Partition indices to arm in "Home" mode
CONF_AWAY_PARTITIONS = "away_partitions"  # Partition indices to arm in "Away" mode
CONF_PARTITION_ARM_MODES = "partition_arm_modes"  # Dict mapping partition index to arm mode ("away" or "home")
