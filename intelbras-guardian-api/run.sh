#!/bin/sh
# ==============================================================================
# Intelbras Guardian API Add-on
# ==============================================================================

# Read configuration from options.json if it exists
CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
    LOG_LEVEL=$(cat "$CONFIG_PATH" | python3 -c "import sys, json; print(json.load(sys.stdin).get('log_level', 'info'))")
else
    LOG_LEVEL="info"
fi

# Set environment variables
export LOG_LEVEL="${LOG_LEVEL}"
export HOST="0.0.0.0"
export PORT="8000"
export DATA_PATH="/data"

# Intelbras API configuration (fixed values)
export INTELBRAS_API_URL="https://api-guardian.intelbras.com.br:8443"
export INTELBRAS_OAUTH_URL="https://api.conta.intelbras.com/auth"
export INTELBRAS_CLIENT_ID="xHCEFEMoQnBcIHcw8ACqbU9aZaYa"

# Other settings
export HTTP_TIMEOUT="30"
export TOKEN_REFRESH_BUFFER="300"
export CORS_ORIGINS="*"
export DEBUG="false"

echo "=============================================="
echo "  Intelbras Guardian API Add-on"
echo "=============================================="
echo "Log level: ${LOG_LEVEL}"
echo "API available at: http://[YOUR_HA_IP]:8000"
echo "=============================================="

# Run the FastAPI application
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
