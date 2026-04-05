#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Boot Alert — runs on VM startup, reports container status to Telegram
# Installed as: /etc/cron.d/alphaagent-boot-alert
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN="8558554522:AAG8dIwXHhXo6p80M0dEXmgtJUF0khrjCms"
CHAT_ID="1222108633"

# Wait for Docker to be ready
sleep 30

UPTIME=$(uptime -p)
BOOT_TIME=$(uptime -s)

# Check all containers
RUNNING=""
STOPPED=""
for c in $(docker ps -a --format '{{.Names}}'); do
    status=$(docker inspect --format='{{.State.Status}}' $c 2>/dev/null)
    if [ "$status" = "running" ]; then
        RUNNING="${RUNNING}  \\u2705 ${c}\n"
    else
        STOPPED="${STOPPED}  \\u274c ${c} (${status})\n"
    fi
done

# Check alphaagent trading agents specifically
AGENTS=""
sleep 5  # Give auto-restart a moment
AGENT_DATA=$(curl -s http://localhost:8030/api/sessions 2>/dev/null)
if [ -n "$AGENT_DATA" ]; then
    AGENTS=$(echo "$AGENT_DATA" | python3 -c "
import sys, json
try:
    sessions = json.load(sys.stdin)
    for s in sessions:
        icon = '\U0001f7e2' if s['is_running'] else '\u26aa'
        print(f'  {icon} {s["session_id"]} (PID {s.get("pid", "?")})')
except: pass
" 2>/dev/null)
fi

# Memory status
MEM=$(free -h | awk '/Mem:/ {printf "%s / %s (%s used)", $3, $2, $3}')
SWAP=$(free -h | awk '/Swap:/ {printf "%s / %s", $3, $2}')

# Build message
MSG="\U0001f504 *VM Restarted*

\U0001f4c5 Boot: ${BOOT_TIME}
\u23f1 Uptime: ${UPTIME}

*Containers Running:*
${RUNNING}"

if [ -n "$STOPPED" ]; then
    MSG="${MSG}
*Containers NOT Running:*
${STOPPED}"
fi

if [ -n "$AGENTS" ]; then
    MSG="${MSG}
*Trading Agents:*
${AGENTS}"
fi

MSG="${MSG}
*Resources:*
  RAM: ${MEM}
  Swap: ${SWAP}"

# Send to Telegram
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"     -d chat_id="${CHAT_ID}"     -d parse_mode="Markdown"     -d text="$(echo -e "$MSG")" > /dev/null 2>&1

