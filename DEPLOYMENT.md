# AlphaAgent — Deployment & Operations Guide

> How the system is deployed, monitored, and maintained.
> Last updated: 2026-04-05

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     USERS (Browser)                      │
│                    alphaagent.skdev.one                   │
└──────────────┬───────────────────────────┬───────────────┘
               │                           │
               ▼                           ▼
┌──────────────────────┐    ┌──────────────────────────────┐
│   Vercel (Frontend)  │    │  GCP VM — e2-medium Spot     │
│                      │    │  IP: 34.139.217.34 (static)  │
│  Next.js 16 + React  │───▶│                              │
│  Auto-deploys from   │    │  ┌────────────────────────┐  │
│  Git push to main    │    │  │ nginx (SSL termination)│  │
│                      │    │  │ alphaagent-api.skdev.one│  │
│  Env:                │    │  └──────────┬─────────────┘  │
│  NEXT_PUBLIC_API_URL │    │             │ :8030           │
│  = alphaagent-api.   │    │  ┌──────────▼─────────────┐  │
│    skdev.one         │    │  │ Docker: alphaagent      │  │
└──────────────────────┘    │  │  ├─ FastAPI (uvicorn)   │  │
                            │  │  ├─ Agent: crypto_test  │  │
                            │  │  ├─ Agent: cryptoaggr.  │  │
                            │  │  └─ Agent: nse_default  │  │
                            │  └────────────────────────┘  │
                            │                              │
                            │  + other project containers  │
                            └──────────────────────────────┘
                                          ▲
                            ┌─────────────┴──────────────┐
                            │  GitHub Actions Watchdog    │
                            │  Checks VM every 5 min     │
                            │  Auto-restarts if stopped   │
                            └────────────────────────────┘
```

---

## Components

### Frontend (Vercel)
- **URL**: https://alphaagent.skdev.one / https://alphaagent-xi.vercel.app
- **Stack**: Next.js 16 + React 19 + Tailwind v4 + TypeScript
- **Deploy**: Auto-deploys on every `git push` to `main`
- **Config**: Root directory = `frontend/` in Vercel project settings
- **Env var**: `NEXT_PUBLIC_API_URL = https://alphaagent-api.skdev.one`

### Backend (GCP VM)
- **URL**: https://alphaagent-api.skdev.one
- **VM**: `socialflow` — e2-medium Spot, us-east1-d, 4 GB RAM
- **IP**: `34.139.217.34` (static, reserved as `socialflow-static-ip`)
- **OS**: Debian 12
- **Stack**: Python 3.13 + FastAPI + Docker + nginx + Let's Encrypt SSL
- **SSH**: `ssh ssh-social` (configured in `~/.ssh/config`)

### Trading Agents
- Spawned as Python subprocesses inside the Docker container
- Each runs `run.py --session <id> --loop`
- 15-minute decision cycles (configurable per session)
- Auto-restart on container restart via `_auto_restart_agents()`

---

## Deploying

### Frontend (automatic)
```bash
git push origin main
# Vercel auto-deploys — no manual step needed
```

### Backend (manual via script)
```bash
# From repo root:
./deploy-prod.sh backend
```

Or manually:
```bash
# 1. Sync files (NEVER use --delete, it can wipe .env)
rsync -avz \
  --include='agent/***' --include='dashboard/***' \
  --include='deploy/***' --include='scripts/***' \
  --include='run.py' --include='requirements.txt' \
  --include='Dockerfile' --include='docker-compose.yml' \
  --include='.dockerignore' --include='CLAUDE.md' \
  --exclude='*' \
  ./ ssh-social:/home/rsumit123/alphaagent/

# 2. Rebuild container
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose up -d --build --force-recreate"
```

### Full deploy (both)
```bash
./deploy-prod.sh all
```

---

## Monitoring & Alerts

### Telegram Bot
- **Bot**: @alphaagent_monitor_bot
- **Alerts sent to**: Sumit's Telegram

### Cron Jobs (on VM)
| Schedule | Script | Purpose |
|----------|--------|---------|
| `@reboot` | `deploy/boot-alert.sh` | Sends container status on every VM restart |
| `*/5 * * * *` | `deploy/monitor.sh` | Alerts on high RAM/swap/disk or containers down |

### monitor.sh alerts when:
- RAM > 85%
- Swap > 80%
- Disk > 90%
- Any container down
- GCP Spot preemption detected
- Rate-limited: max 1 alert per hour

### GitHub Actions Watchdog
- **File**: `.github/workflows/vm-watchdog.yml`
- **Runs**: Every 5 minutes via cron
- **Does**: Checks VM status via GCP API. If TERMINATED/STOPPED, restarts it and sends Telegram alert
- **Service Account**: `vm-watchdog@polar-pillar-450607-b7.iam.gserviceaccount.com`

### Full Recovery Flow (Spot Preemption)
```
GCP preempts VM
  ↓ (within 5 min)
GitHub Actions detects TERMINATED → runs gcloud instances start
  ↓ Telegram: "Auto-Restart Triggered"
VM boots → Docker restarts all containers
  ↓
_auto_restart_agents() relaunches trading agents
  ↓
boot-alert.sh → Telegram: "VM Restarted — containers up"
  ↓
Total downtime: ~3-7 minutes
```

---

## Session Data & Persistence

### Where data lives
```
/home/rsumit123/alphaagent/sessions/
├── crypto_test/
│   ├── config.yaml          # Session configuration
│   ├── trades.db            # SQLite — trades, positions, snapshots
│   ├── journal.md           # Learning journal + distilled rules
│   ├── agent.log            # Agent process log
│   ├── agent.lock           # PID lock file (runtime)
│   └── agent_stderr.log     # Python tracebacks
├── cryptoaggressive/
└── nse_default/
```

### Docker volume mounts (docker-compose.yml)
```yaml
volumes:
  - ./sessions:/app/sessions
  - ./data:/app/data
  - ./learnings:/app/learnings
  - ./logs:/app/logs
```

Data survives: container restart, rebuild, VM reboot, Spot preemption.

### What NEVER to sync to production
- `sessions/` — would overwrite live trade data
- `.env` — contains API keys
- `frontend/` — deployed via Vercel, not the VM

---

## Agent Learning Pipeline

```
Trade Entry → Journal log (thesis, conviction 1-5)
     ↓
Trade Exit → LLM reflection + exit_type tag (stop_hit/target_hit/manual/forced)
     ↓
Daily Review → LLM analyzes trades WITH quantitative stats (win rate by direction, type, exit)
     ↓
Distilled Rules → 20 data-backed bullet points (regenerated from full history)
     ↓
Next Cycle → Rules injected into system prompt
     ↓
Decision → LLM must check rules before trading, explain any overrides
```

### Key improvements over naive approach
1. **Quantitative stats** fed into review/rules prompts (not just narrative)
2. **Conviction tracking** — LLM rates confidence 1-5, correlated with outcomes
3. **Exit type tracking** — structured stop_hit/target_hit enables "are stops too tight?" analysis
4. **Rule enforcement** — system prompt explicitly requires checking distilled rules
5. **24/7 daily review trigger** — crypto markets get reviews via `_maybe_run_daily_review()`

---

## DNS

| Subdomain | Type | Points to | Managed at |
|-----------|------|-----------|------------|
| alphaagent.skdev.one | CNAME | cname.vercel-dns.com | Namecheap |
| alphaagent-api.skdev.one | A | 34.139.217.34 | Namecheap |

The API IP is static (GCP reservation). Does NOT change across Spot preemptions.

---

## Cost Breakdown

| Component | Monthly (approx) |
|-----------|-----------------|
| GCP e2-medium Spot compute | ~₹600-800 |
| GCP Static IP + network | ~₹300-400 |
| GCP Disk (10 GB) | ~₹70 |
| Vercel (frontend) | Free |
| GitHub Actions (watchdog) | Free |
| OpenRouter LLM calls | ~₹200-500 (varies by model + activity) |
| **Total** | **~₹1,200-1,800/mo** |

---

## Common Operations

```bash
# Check agent status
curl -s https://alphaagent-api.skdev.one/api/sessions | python3 -m json.tool

# View agent logs
ssh ssh-social "tail -50 /home/rsumit123/alphaagent/sessions/crypto_test/agent.log"

# Restart a specific agent
curl -X POST https://alphaagent-api.skdev.one/api/agent/stop/crypto_test
sleep 3
curl -X POST https://alphaagent-api.skdev.one/api/agent/start/crypto_test

# Check VM resources
ssh ssh-social "free -h && docker stats --no-stream"

# Force restart VM (if unresponsive)
gcloud compute instances reset socialflow --zone us-east1-d

# Deploy backend
./deploy-prod.sh backend

# Renew SSL cert
ssh ssh-social "sudo certbot renew"
```
