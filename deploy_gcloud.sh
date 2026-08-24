#!/usr/bin/env bash
# deploy_gcloud.sh — One-shot deploy for Google Cloud Shell

set -e

REPO_URL="https://github.com/XKILLER2006Y/TeraBox-Video-Downloader.git"
BRANCH="main"
BOT_DIR="$HOME/terabox-bot"
ENV_FILE="$BOT_DIR/.env"

cd "$HOME" || exit 1

echo "╔══════════════════════════════════════════════════════╗"
echo "║  TeraBox Bot — Google Cloud Shell Deploy            ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── Step 1: Clone or pull repo ─────────────────────────────────────────────────———————————————
if [ -d "$BOT_DIR/.git" ]; then
    echo "[1/8] Repo exists, pulling latest..."
    cd "$BOT_DIR"
    git stash -q 2>/dev/null || true
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "[1/8] Cloning repo..."
    git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$BOT_DIR"
    cd "$BOT_DIR"
fi

# ── Step 2: Install system dependencies ─────────────────────────────────—————
echo "[2/8] Installing system dependencies..."
# Suppress Cloud Shell's 5-second apt-get warning banner (also helps the
# .bashrc auto-start hook reinstall screen quietly on session recycle)
mkdir -p "$HOME/.cloudshell" && touch "$HOME/.cloudshell/no-apt-get-warning"
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

# ── Step 4: Create .env if missing ─────────────────────────────────────────———
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/8] Creating .env template..."
    cp .env.example .env
    echo "  → Created $ENV_FILE"
    echo "  ⚠️  EDIT IT NOW: nano $ENV_FILE"
    echo "      Required: BOT_TOKEN, APP_ID, API_HASH, STORAGE_GROUP_ID, FIREBASE_SECRETS"
else
    echo "[4/8] .env file exists."
fi

# Check that each required key exists and has a non-empty value.
MISSING_KEYS=0
for KEY in BOT_TOKEN APP_ID API_HASH STORAGE_GROUP_ID FIREBASE_SECRETS; do
    VAL=$(grep -E "^${KEY}=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '"')
    if [ -z "$VAL" ]; then
        echo "     ⚠️  Missing or empty: $KEY"
        MISSING_KEYS=1
    fi
done

if [ "$MISSING_KEYS" -eq 1 ]; then
    echo ""
    read -r -p "❓ Fill missing keys now? [y/N]: " ANSWER
    if [[ "$ANSWER" == "y" || "$ANSWER" == "Y" ]]; then
        nano "$ENV_FILE"
    else
        echo "  ⚠️  Bot may fail to start with empty keys. Edit later: nano $ENV_FILE"
    fi
fi

# ── Step 5: Stop existing bot (if any) ─────────——————————————————————————
echo "[5/8] Stopping any existing bot..."
pkill -f "terabox-bot/venv/bin/python main.py" 2>/dev/null && sleep 2 || true
screen -S terabox -X quit 2>/dev/null || true
screen -S keepalive -X quit 2>/dev/null || true

# ── Step 6: Create run.sh wrapper (auto-restart on crash) ─———————————————————
echo "[6/8] Creating auto-restart wrapper..."
cat > "$BOT_DIR/run.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
while true; do
    echo "[$(date)] Starting bot..." >> bot.log
    python main.py >> bot.log 2>&1
    EXIT_CODE=$?
    echo "[$(date)] Bot exited with code $EXIT_CODE — restarting in 5s" >> bot.log
    # Exit loop cleanly if user pressed Ctrl+C inside screen (exit code 130 = SIGINT)
    if [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 0 ]; then
        break
    fi
    sleep 5
done
EOF
chmod +x "$BOT_DIR/run.sh"

# ── Step 7: Keepalive cron (prevent idle timeout) ─———————————————————————————
echo "[7/8] Setting up keepalive (prevents idle timeout)..."
cat > "$BOT_DIR/cloud_shell_keepalive.sh" << 'EOF'
#!/bin/bash
# Keeps Cloud Shell session alive by touching a file every 4 minutes.
# Runs inside a screen session; started by deploy_gcloud.sh and the
# .bashrc auto-start hook.
while true; do
    touch "$HOME/.keepalive"
    sleep 240
done
EOF
chmod +x "$BOT_DIR/cloud_shell_keepalive.sh"

cat > "$HOME/.crontab.txt" << EOF
*/4 * * * * /bin/touch $HOME/.keepalive
EOF
crontab "$HOME/.crontab.txt" 2>/dev/null || true
rm -f "$HOME/.crontab.txt"

# Also start keepalive in a screen session right away
screen -dmS keepalive bash -c "$BOT_DIR/cloud_shell_keepalive.sh" 2>/dev/null || true

# ── Step 8: Start bot ─———————————————————————————————————————————————————————————————
echo "[8/8] Starting bot in screen session..."
screen -dmS terabox bash -c "$BOT_DIR/run.sh"
sleep 4

# ── Install .bashrc auto-restart hook ─────────────────────────────────————————————————
# Always refresh the block so existing installs get the latest version.
sed -i '/# >>> terabox-bot auto-start/,/# <<< terabox-bot auto-start/d' "$HOME/.bashrc" 2>/dev/null
cat >> "$HOME/.bashrc" <<'BASHRCEOF'

# >>> terabox-bot auto-start (managed by deploy_gcloud.sh) >>>
# When Cloud Shell restarts (after 12h timeout), auto-restart the bot.
# Self-healing: reinstalls screen (system pkgs don't survive session recycle),
# falls back to nohup if screen still unavailable.
_terabox_autostart() {
    if ! command -v screen >/dev/null 2>&1; then
        sudo apt-get install -y -qq screen >/dev/null 2>&1 || true
    fi
    if command -v screen >/dev/null 2>&1; then
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
    else
        # Fallback: run without screen so the bot still comes up
        if [ -f "$HOME/terabox-bot/run.sh" ] && ! pgrep -f "terabox-bot/run.sh" >/dev/null 2>&1; then
            echo "[auto-start] screen unavailable — starting with nohup..."
            nohup bash "$HOME/terabox-bot/run.sh" >> "$HOME/terabox-bot/bot.log" 2>&1 &
        fi
    fi
}
_terabox_autostart
unset -f _terabox_autostart
# <<< terabox-bot auto-start <<<
BASHRCEOF
echo "  → .bashrc auto-start hook installed."

# ── Verify ─────────────────────────────────———————————————————————————————————————
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
