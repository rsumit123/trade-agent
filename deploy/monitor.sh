#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# AlphaAgent VM Monitor — sends Telegram alerts on issues
# Runs via cron every 5 minutes
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN="8558554522:AAG8dIwXHhXo6p80M0dEXmgtJUF0khrjCms"
CHAT_ID="1222108633"
ALERT_FILE="/tmp/alphaagent_alert_sent"
MEM_THRESHOLD=85
SWAP_THRESHOLD=80

# Get resource usage
MEM_PCT=$(free | awk '/Mem:/ {printf "%.0f", $3/$2*100}')
SWAP_PCT=$(free | awk '/Swap:/ {if($2>0) printf "%.0f", $3/$2*100; else print "0"}')
DISK_PCT=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')

# Check GCP preemption signal (Spot VM warning)
PREEMPT=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/preempted 2>/dev/null)

# Check all containers
DOWN_CONTAINERS=""
for c in $(docker ps -a --format '{{.Names}}'); do
    status=$(docker inspect --format='{{.State.Status}}' $c 2>/dev/null)
    if [ "$status" != "running" ]; then
        DOWN_CONTAINERS="${DOWN_CONTAINERS}${c} (${status}), "
    fi
done

# Check alphaagent container specifically
AGENT_CONTAINER=$(docker inspect --format='{{.State.Status}}' alphaagent-backend 2>/dev/null || echo "not_found")

# Count running trading agents
RUNNING_AGENTS=$(curl -s http://localhost:8030/api/sessions 2>/dev/null | python3 -c "import sys,json; print(sum(1 for s in json.load(sys.stdin) if s.get('is_running')))" 2>/dev/null || echo "?")

# Build alert if thresholds exceeded
ALERT=""

if [ "$PREEMPT" = "TRUE" ]; then
    ALERT="${ALERT}\U0001f6a8 SPOT PREEMPTION: VM is being terminated by GCP!\n"
fi

if [ "$MEM_PCT" -ge "$MEM_THRESHOLD" ]; then
    ALERT="${ALERT}\U0001f534 RAM: ${MEM_PCT}% (threshold: ${MEM_THRESHOLD}%)\n"
fi

if [ "$SWAP_PCT" -ge "$SWAP_THRESHOLD" ]; then
    ALERT="${ALERT}\U0001f7e1 Swap: ${SWAP_PCT}% (threshold: ${SWAP_THRESHOLD}%)\n"
fi

if [ "$DISK_PCT" -ge 90 ]; then
    ALERT="${ALERT}\U0001f534 Disk: ${DISK_PCT}%\n"
fi

if [ "$AGENT_CONTAINER" != "running" ]; then
    ALERT="${ALERT}\U0001f480 AlphaAgent container: ${AGENT_CONTAINER}\n"
fi

if [ -n "$DOWN_CONTAINERS" ]; then
    ALERT="${ALERT}\u26a0\ufe0f Down containers: ${DOWN_CONTAINERS%%, }\n"
fi

# Send alert if there is something to report
if [ -n "$ALERT" ]; then
    if [ ! -f "$ALERT_FILE" ] || [ $(( $(date +%s) - $(stat -c %Y "$ALERT_FILE" 2>/dev/null || echo 0) )) -gt 3600 ]; then
        MSG="\u26a0\ufe0f *AlphaAgent VM Alert*\n\n${ALERT}\n\U0001f4ca Agents running: ${RUNNING_AGENTS}\nRAM: ${MEM_PCT}% | Swap: ${SWAP_PCT}% | Disk: ${DISK_PCT}%"

        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"             -d chat_id="${CHAT_ID}"             -d parse_mode="Markdown"             -d text="$(echo -e "$MSG")" > /dev/null

        touch "$ALERT_FILE"
    fi
else
    rm -f "$ALERT_FILE"
fi
