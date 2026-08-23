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
    echo "[1/7] Repo exists, pulling latest..."
    cd "$BOT_DIR"
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "[1/7] Cloning repo..."
    rm -rf "$BOT_DIR"
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$BOT_DIR"
    cd "$BOT_DIR"
fi

# ── Step 2: Install system dependencies ──────────────────────────────────────
echo "[2/7] Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ffmpeg python3-pip python3-venv screen > /dev/null 2>&1 || {
        echo "  ⚠️  apt-get had issues, retrying..."
        sudo apt-get install -y ffmpeg python3-pip python3-venv screen
    }
elif command -v apk &>/dev/null; then
    apk add --no-cache ffmpeg python3 py3-pip > /dev/null 2>&1
fi

# Verify screen is available
if ! command -v screen &>/dev/null; then
    echo "  ❌ screen not found. Installing..."
    sudo apt-get install -y screen
fi

# ── Step 3: Create venv and install Python deps ──────────────────────────────
echo "[3/7] Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  → Python deps installed."

# ── Step 4: Create .env if missing ────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/7] Creating .env template..."
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
THREADPOOL_SIZE=8
CONN_POOL_SIZE=5
FIRESTORE_DB_ID=diskwala
COOKIES1=
PROXY_URL=
DISKWALA_PROXY_URL=
DISKWALA_API_KEY=
ENVEOF
    echo ""
    echo "  ⚠️  .env file created! Edit it with your credentials:"
    echo "     nano $ENV_FILE"
    echo ""
    echo "  After editing, re-run this script."
    exit 0
else
    echo "[4/7] .env file exists."
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
echo "[5/7] Stopping any existing bot..."
screen -S terabox -X quit 2>/dev/null || true
pkill -f "python main.py" 2>/dev/null || true
sleep 1

# ── Step 6: Create auto-restart wrapper ───────────────────────────────────────
echo "[6/7] Creating auto-restart wrapper..."
cat > "$BOT_DIR/run.sh" <<'RUNEOF'
#!/usr/bin/env bash
# Auto-restart wrapper — restarts bot on crash
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
export PORT="${PORT:-8080}"

echo "[$(date)] Bot starting..."
while true; do
    python main.py 2>&1 | tee -a bot.log
    EXIT_CODE=$?
    echo "[$(date)] Bot exited with code $EXIT_CODE. Restarting in 5s..."
    sleep 5
done
RUNEOF
chmod +x "$BOT_DIR/run.sh"

# ── Step 7: Start bot in screen ───────────────────────────────────────────────
echo "[7/7] Starting bot in screen session..."
screen -dmS terabox bash -c "$BOT_DIR/run.sh"
sleep 4

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
    echo "  Features enabled:"
    echo "  • Auto-restart on crash (run.sh wrapper)"
    echo "  • Log rotation (5MB max, 2 backups)"
    echo "  • Memory monitoring (every 5 min)"
    echo "  • Aggressive storage cleanup (every 2 min)"
    echo ""
    echo "  To restart after Cloud Shell restarts:"
    echo "     cd $BOT_DIR && bash deploy_gcloud.sh"
else
    echo ""
    echo "  ❌ Bot failed to start. Check logs:"
    echo "     cat $BOT_DIR/bot.log"
fi
