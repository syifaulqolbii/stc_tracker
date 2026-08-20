#!/bin/bash
# Moban FU Case Tracker — Production startup script
# Usage: ./run.sh

set -e

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Default values
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-2}"
LOG_LEVEL="${LOG_LEVEL:-info}"

echo "Starting Moban FU Tracker..."
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Workers: $WORKERS"
echo "  Log Level: $LOG_LEVEL"
echo ""

# Run with uvicorn
exec uvicorn main-v1-1:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    --log-level "$LOG_LEVEL" \
    --access-log \
    --proxy-headers \
    --forwarded-allow-ips='*'
