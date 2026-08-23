#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Start/restart the bot. Run this if the bot crashes or after Cloud Shell restarts.
# Usage: bash start.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$BOT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env not found. Run deploy_gcloud.sh first."
    exit 1
fi

# Stop existing
screen -S terabox -X quit 2>/dev/null || true
pkill -f "python main.py" 2>/dev/null || true
sleep 1

# Start
source "$ENV_FILE" 2>/dev/null || true
export PORT="${PORT:-8080}"
cd "$BOT_DIR"
source venv/bin/activate
screen -dmS terabox bash -c "python main.py 2>&1 | tee -a bot.log"
sleep 2

if screen -ls 2>/dev/null | grep -q terabox; then
    echo "✅ Bot running. screen -r terabox to attach."
else
    echo "❌ Failed. Check: cat bot.log"
fi
