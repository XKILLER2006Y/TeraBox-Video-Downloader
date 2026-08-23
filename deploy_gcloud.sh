#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Google Cloud Shell One-Click Deploy
# Paste this entire script into Cloud Shell to deploy the bot.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BOT_DIR="$HOME/terabox-bot"
REPO_URL="https://github.com/XKILLER2006Y/TeraBox-Video-Downloader.git"
BRANCH="main"
ENV_FILE="$BOT_DIR/.env"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  TeraBox Bot — Google Cloud Shell Deploy            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Clone or update repo ─────────────────────────────────────────────
if [ -d "$BOT_DIR/.git" ]; then
    echo "[1/6] Repo exists, pulling latest..."
    cd "$BOT_DIR"
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "[1/6] Cloning repo..."
    rm -rf "$BOT_DIR"
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$BOT_DIR"
    cd "$BOT_DIR"
fi

# ── Step 2: Install system dependencies ──────────────────────────────────────
echo "[2/6] Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ffmpeg python3-pip python3-venv > /dev/null 2>&1
elif command -v apk &>/dev/null; then
    apk add --no-cache ffmpeg python3 py3-pip > /dev/null 2>&1
fi

# ── Step 3: Create venv and install Python deps ──────────────────────────────
echo "[3/6] Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  → Python deps installed."

# ── Step 4: Create .env if missing ────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/6] Creating .env template..."
    cat > "$ENV_FILE" <<'ENVEOF'
# ── Required ──────────────────────────────────────────────────────────────────
BOT_TOKEN=
APP_ID=
API_HASH=
STORAGE_GROUP_ID=
FIREBASE_SECRETS=

# ── Optional ──────────────────────────────────────────────────────────────────
ADMIN_ID=
PORT=8080
COOKIES1=
COOKIES2=
PROXY_URL=
DISKWALA_PROXY_URL=
DISKWALA_API_KEY=
ENVEOF
    echo ""
    echo "  ⚠️  .env file created! Edit it with your credentials:"
    echo "     nano $ENV_FILE"
    echo ""
    echo "  Required fields:"
    echo "    BOT_TOKEN  — from @BotFather"
    echo "    APP_ID     — from https://my.telegram.org"
    echo "    API_HASH   — from https://my.telegram.org"
    echo "    STORAGE_GROUP_ID — your storage channel/group ID (e.g. -1001234567890)"
    echo "    FIREBASE_SECRETS — Firebase service account JSON (paste entire JSON)"
    echo ""
    echo "  After editing, re-run this script."
    exit 0
else
    echo "[4/6] .env file exists."
fi

# ── Validate required vars ────────────────────────────────────────────────────
source <(grep -v '^\s*#' "$ENV_FILE" | sed 's/^/export /')
missing=()
[ -z "${BOT_TOKEN:-}" ] && missing+=("BOT_TOKEN")
[ -z "${APP_ID:-}" ] && missing+=("APP_ID")
[ -z "${API_HASH:-}" ] && missing+=("API_HASH")
[ -z "${STORAGE_GROUP_ID:-}" ] && missing+=("STORAGE_GROUP_ID")
[ -z "${FIREBASE_SECRETS:-}" ] && missing+=("FIREBASE_SECRETS")

if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "  ❌ Missing required env vars: ${missing[*]}"
    echo "  Edit $ENV_FILE and re-run."
    exit 1
fi

# ── Step 5: Stop any existing bot ─────────────────────────────────────────────
echo "[5/6] Stopping any existing bot..."
screen -S terabox -X quit 2>/dev/null || true
pkill -f "python main.py" 2>/dev/null || true
sleep 1

# ── Step 6: Start bot in screen ───────────────────────────────────────────────
echo "[6/6] Starting bot in screen session..."
export PORT="${PORT:-8080}"
screen -dmS terabox bash -c "cd $BOT_DIR && source venv/bin/activate && python main.py 2>&1 | tee -a bot.log"
sleep 3

# ── Verify ────────────────────────────────────────────────────────────────────
if screen -ls 2>/dev/null | grep -q terabox; then
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║  ✅ Bot is running!                                 ║"
    echo "╠══════════════════════════════════════════════════════╣"
    echo "║  Screen session: screen -r terabox                  ║"
    echo "║  Logs: tail -f $BOT_DIR/bot.log                     ║"
    echo "║  Stop: screen -S terabox -X quit                    ║"
    echo "╚══════════════════════════════════════════════════════╝"
    echo ""
    echo "  Next steps:"
    echo "  1. Verify bot responds: send /start in Telegram"
    echo "  2. Set up UptimeRobot (free) to keep it alive:"
    echo "     → Sign up at https://uptimerobot.com (free, no card)"
    echo "     → Add HTTP monitor"
    echo "     → URL: http://localhost:${PORT}/ping (or your Cloud Shell URL)"
    echo "     → Interval: 5 minutes"
    echo ""
    echo "  ⚠️  Google Cloud Shell sleeps after 12h idle."
    echo "     UptimeRobot pings prevent sleep."
    echo ""
    echo "  To restart after Cloud Shell restarts:"
    echo "     cd $BOT_DIR && bash deploy_gcloud.sh"
else
    echo ""
    echo "  ❌ Bot failed to start. Check logs:"
    echo "     cat $BOT_DIR/bot.log"
fi
