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
    echo "[1/8] Repo exists, pulling latest..."
    cd "$BOT_DIR"
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "[1/8] Cloning repo..."
    rm -rf "$BOT_DIR"
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$BOT_DIR"
    cd "$BOT_DIR"
fi

# ── Step 2: Install system dependencies ──────────────────────────────────────
echo "[2/8] Installing system dependencies..."
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
echo "[3/8] Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  → Python deps installed."

# ── Step 4: Create .env if missing ────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/8] Creating .env template..."
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
    echo "[4/8] .env file exists."
fi

# ── Validate required vars ───────────────────────────────────────────────────
# Check that each required key exists and has a non-empty value.
# FIREBASE_SECRETS can be multi-line JSON, so we use grep + wc to check.
missing=()

check_env_key() {
    local key="$1"
    # Get the value: everything after first '=' up to next key or EOF
    # Use awk to extract the value (handles multi-line values)
    local val
    val=$(awk -v key="^${key}=" '
        $0 ~ key { found=1; sub(key, ""); val=$0; next }
        found && /^[A-Z_]+=/ { exit }
        found { val = val "\n" $0 }
        END { print val }
    ' "$ENV_FILE" | tr -d '[:space:]')
    [ -z "$val" ] && missing+=("$key")
}

check_env_key "BOT_TOKEN"
check_env_key "APP_ID"
check_env_key "API_HASH"
check_env_key "STORAGE_GROUP_ID"
check_env_key "FIREBASE_SECRETS"

if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "  ❌ Missing required env vars: ${missing[*]}"
    echo "  Edit $ENV_FILE and re-run."
    exit 1
fi

# ── Note: .env is read by python-dotenv at runtime (handles multi-line JSON) ──
# FIREBASE_SECRETS can contain newlines — bash source cannot handle this.
# python-dotenv in db.py handles it correctly.

# ── Step 5: Stop any existing bot ─────────────────────────────────────────────
echo "[5/8] Stopping any existing bot..."
screen -S terabox -X quit 2>/dev/null || true
screen -S keepalive -X quit 2>/dev/null || true
pkill -f "python main.py" 2>/dev/null || true
pkill -f "cloud_shell_keepalive" 2>/dev/null || true
sleep 1

# ── Step 6: Create auto-restart wrapper ───────────────────────────────────────
echo "[6/8] Creating auto-restart wrapper..."
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

# ── Step 7: Create keepalive script (prevents 20-min idle timeout) ───────────
echo "[7/8] Setting up keepalive (prevents idle timeout)..."
cat > "$BOT_DIR/cloud_shell_keepalive.sh" <<'KEEPALIVEOF'
#!/usr/bin/env bash
# Cloud Shell Keepalive — prevents idle timeout by touching files periodically.
# Runs in a screen session named "keepalive".
# Cloud Shell kills idle sessions after ~20 min. This script touches a file
# every 5 minutes to simulate activity, extending the session to its full
# 12-hour lifetime.
KEEPALIVE_DIR="$HOME/.cloudshell"
mkdir -p "$KEEPALIVE_DIR"
echo "[$(date)] Keepalive started. Touching every 5 minutes."
while true; do
    touch "$KEEPALIVE_DIR/keepalive_$(date +%s)"
    # Clean up old keepalive files (keep last 10)
    ls -t "$KEEPALIVE_DIR"/keepalive_* 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
    sleep 300
done
KEEPALIVEOF
chmod +x "$BOT_DIR/cloud_shell_keepalive.sh"

# Start keepalive in its own screen session
screen -dmS keepalive bash -c "$BOT_DIR/cloud_shell_keepalive.sh"
echo "  → Keepalive screen session running."

# ── Step 8: Start bot in screen ───────────────────────────────────────────────
echo "[8/8] Starting bot in screen session..."
screen -dmS terabox bash -c "$BOT_DIR/run.sh"
sleep 4

# ── Install .bashrc auto-restart hook ─────────────────────────────────────────
BASHRC_MARKER="# >>> terabox-bot auto-start (managed by deploy_gcloud.sh) >>>"
if ! grep -qF "$BASHRC_MARKER" "$HOME/.bashrc" 2>/dev/null; then
    cat >> "$HOME/.bashrc" <<'BASHRCEOF'

# >>> terabox-bot auto-start (managed by deploy_gcloud.sh) >>>
# When Cloud Shell restarts (after 12h timeout), auto-restart the bot.
_terabox_autostart() {
    if ! screen -ls 2>/dev/null | grep -q terabox; then
        if [ -f "$HOME/terabox-bot/run.sh" ]; then
            echo "[auto-start] Starting terabox bot..."
            screen -dmS terabox bash -c "$HOME/terabox-bot/run.sh"
        fi
    fi
    # Also ensure keepalive is running
    if ! screen -ls 2>/dev/null | grep -q keepalive; then
        if [ -f "$HOME/terabox-bot/cloud_shell_keepalive.sh" ]; then
            echo "[auto-start] Starting keepalive..."
            screen -dmS keepalive bash -c "$HOME/terabox-bot/cloud_shell_keepalive.sh"
        fi
    fi
}
_terabox_autostart
unset -f _terabox_autostart
# <<< terabox-bot auto-start <<<
BASHRCEOF
    echo "  → .bashrc auto-start hook installed."
else
    echo "  → .bashrc hook already present."
fi

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
    echo "  Features:"
    echo "  • Auto-restart on crash (run.sh wrapper)"
    echo "  • Keepalive cron (prevents 20-min idle kill → full 12h)"
    echo "  • Auto-start on Cloud Shell reopen (.bashrc hook)"
    echo ""
    echo "  ⚠️  Cloud Shell has a hard 12h lifetime."
    echo "     After 12h, just reopen Cloud Shell — bot auto-restarts."
else
    echo ""
    echo "  ❌ Bot failed to start. Check logs:"
    echo "     cat $BOT_DIR/bot.log"
fi
