#!/bin/bash
# AlphaAgent VM Monitor — sends Telegram alerts on high resource usage
# Runs via cron every 5 minutes

BOT_TOKEN="8558554522:AAG8dIwXHhXo6p80M0dEXmgtJUF0khrjCms"
CHAT_ID="1222108633"
ALERT_FILE="/tmp/alphaagent_alert_sent"
MEM_THRESHOLD=85
SWAP_THRESHOLD=80

# Get memory usage percentage
MEM_PCT=$(free | awk '/Mem:/ {printf "%.0f", $3/$2*100}')
SWAP_PCT=$(free | awk '/Swap:/ {if($2>0) printf "%.0f", $3/$2*100; else print "0"}')
DISK_PCT=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')

# Check if container is healthy
CONTAINER_STATUS=$(docker inspect --format='{{.State.Status}}' alphaagent-backend 2>/dev/null || echo "not_found")

# Count running agents
RUNNING_AGENTS=$(curl -s http://localhost:8030/api/sessions 2>/dev/null | python3 -c "import sys,json; print(sum(1 for s in json.load(sys.stdin) if s.get('is_running')))" 2>/dev/null || echo "?")

# Build alert message if thresholds exceeded
ALERT=""

if [ "$MEM_PCT" -ge "$MEM_THRESHOLD" ]; then
    ALERT="${ALERT}🔴 RAM: ${MEM_PCT}% (threshold: ${MEM_THRESHOLD}%)\n"
fi

if [ "$SWAP_PCT" -ge "$SWAP_THRESHOLD" ]; then
    ALERT="${ALERT}🟡 Swap: ${SWAP_PCT}% (threshold: ${SWAP_THRESHOLD}%)\n"
fi

if [ "$DISK_PCT" -ge 90 ]; then
    ALERT="${ALERT}🔴 Disk: ${DISK_PCT}%\n"
fi

if [ "$CONTAINER_STATUS" != "running" ]; then
    ALERT="${ALERT}💀 Container: ${CONTAINER_STATUS}\n"
fi

# Send alert if there's something to report
if [ -n "$ALERT" ]; then
    # Only send once per hour (don't spam)
    if [ ! -f "$ALERT_FILE" ] || [ $(( $(date +%s) - $(stat -c %Y "$ALERT_FILE" 2>/dev/null || echo 0) )) -gt 3600 ]; then
        MSG="⚠️ *AlphaAgent VM Alert*\n\n${ALERT}\n📊 Agents running: ${RUNNING_AGENTS}\nRAM: ${MEM_PCT}% | Swap: ${SWAP_PCT}% | Disk: ${DISK_PCT}%\nContainer: ${CONTAINER_STATUS}"
        
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"             -d chat_id="${CHAT_ID}"             -d parse_mode="Markdown"             -d text="$(echo -e "$MSG")" > /dev/null
        
        touch "$ALERT_FILE"
    fi
else
    # Clear alert file when everything is OK (so next alert sends immediately)
    rm -f "$ALERT_FILE"
fi
