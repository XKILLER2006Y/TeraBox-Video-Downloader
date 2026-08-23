# Deploy to Google Cloud Shell (Free, No Credit Card)

## What You Get
- **Cost:** $0 forever (Google gives 1200 Cloud Shell-hours/month free)
- **Specs:** 1 vCPU, 2GB RAM, 5GB persistent disk
- **Docker:** Not needed — runs Python directly
- **Limitation:** Sleeps after 12h idle (keep-alive via UptimeRobot solves this)

## Step-by-Step (From Phone)

### 1. Open Google Cloud Shell
- Open Chrome on your phone
- Go to **https://shell.cloud.google.com**
- Sign in with your Google account (Gmail)
- Accept the terms if prompted

### 2. Clone the Bot
Paste this into the Cloud Shell terminal:
```bash
bash <(curl -s https://raw.githubusercontent.com/XKILLER2006Y/TeraBox-Video-Downloader/main/deploy_gcloud.sh)
```

**OR** if the above doesn't work, paste line by line:
```bash
git clone --depth 1 https://github.com/XKILLER2006Y/TeraBox-Video-Downloader.git ~/terabox-bot
cd ~/terabox-bot
bash deploy_gcloud.sh
```

### 3. Fill In Credentials
The script creates a `.env` file. Edit it:
```bash
nano ~/terabox-bot/.env
```

Fill in these values:
| Variable | Where to Get |
|----------|-------------|
| `BOT_TOKEN` | @BotFather in Telegram → /newbot |
| `APP_ID` | https://my.telegram.org → API Development Tools |
| `API_HASH` | Same page as APP_ID |
| `STORAGE_GROUP_ID` | Forward a message from your storage channel to @userinfobot |
| `FIREBASE_SECRETS` | Paste entire Firebase service account JSON |

Save: `Ctrl+O`, `Enter`, `Ctrl+X`

### 4. Run the Script Again
```bash
cd ~/terabox-bot && bash deploy_gcloud.sh
```

### 5. Verify Bot Works
- Open Telegram
- Send `/start` to your bot
- It should respond

### 6. Set Up Keep-Alive (UptimeRobot)
This prevents Cloud Shell from sleeping:

1. Go to **https://uptimerobot.com** on your phone
2. Sign up (free, no card needed)
3. Add New Monitor → Type: **HTTP(s)**
4. Friendly Name: `TeraBox Bot`
5. URL: your Cloud Shell external URL (see below)
6. Monitoring Interval: **5 minutes**
7. Save

**To get your Cloud Shell URL:**
```bash
echo "Your bot URL: https://$(hostname)-$(echo $CLOUD_SHELL_PORT || echo 8080).cloudshell.dev/ping"
```

Or in Cloud Shell, click **Web Preview** (icon top-right) → **Preview on port 8080**

### 7. Useful Commands
```bash
# Attach to bot screen
screen -r terabox

# Detach from screen (keeps bot running)
# Press Ctrl+A then D

# Check bot logs
tail -f ~/terabox-bot/bot.log

# Stop bot
screen -S terabox -X quit

# Restart bot
cd ~/terabox-bot && bash start.sh
```

## After Cloud Shell Restarts
Cloud Shell sometimes restarts. When it does:
```bash
cd ~/terabox-bot && bash deploy_gcloud.sh
```
This re-clones (or pulls) and restarts the bot. Takes ~30 seconds.

## Troubleshooting

**Bot not responding?**
```bash
tail -20 ~/terabox-bot/bot.log
```

**Missing dependencies?**
```bash
cd ~/terabox-bot && source venv/bin/activate && pip install -r requirements.txt
```

**Screen not found?**
```bash
sudo apt-get install -y screen
```
