#!/bin/bash
# Boot Alert — runs on VM startup, reports container status to Telegram
# Installed as @reboot cron job

# Telegram credentials live in the app's .env (never in git)
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'"; }
BOT_TOKEN="$(_env TELEGRAM_BOT_TOKEN)"
CHAT_ID="$(_env TELEGRAM_CHAT_ID)"

# Wait for Docker to be ready
sleep 30

BOOT_TIME=$(uptime -s)
UPTIME=$(uptime -p)

# Check all containers
RUNNING=""
STOPPED=""
for c in $(docker ps -a --format '{{.Names}}'); do
    status=$(docker inspect --format='{{.State.Status}}' "$c" 2>/dev/null)
    if [ "$status" = "running" ]; then
        RUNNING="$RUNNING  ✅ $c
"
    else
        STOPPED="$STOPPED  ❌ $c ($status)
"
    fi
done

# Check trading agents
sleep 5
AGENTS=""
AGENT_DATA=$(curl -s http://localhost:8030/api/sessions 2>/dev/null)
if [ -n "$AGENT_DATA" ]; then
    AGENTS=$(echo "$AGENT_DATA" | python3 -c "
import sys, json
try:
    for s in json.load(sys.stdin):
        icon = chr(0x1F7E2) if s['is_running'] else chr(0x26AA)
        print(f'  {icon} {s[\"session_id\"]} (PID {s.get(\"pid\", \"?\")})')
except: pass
" 2>/dev/null)
fi

# Memory
MEM=$(free -h | awk '/Mem:/ {print $3 " / " $2}')
SWAP=$(free -h | awk '/Swap:/ {print $3 " / " $2}')

# Build message
MSG="🔄 VM Restarted

📅 Boot: $BOOT_TIME
⏱ $UPTIME

Containers Running:
$RUNNING"

if [ -n "$STOPPED" ]; then
    MSG="${MSG}
Containers Stopped:
$STOPPED"
fi

if [ -n "$AGENTS" ]; then
    MSG="${MSG}
Trading Agents:
$AGENTS
"
fi

MSG="${MSG}
Resources:
  RAM: $MEM
  Swap: $SWAP"

# Send as plain text (no parse_mode — avoids Markdown escaping issues)
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${MSG}" > /dev/null 2>&1
