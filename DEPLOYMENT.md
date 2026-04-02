# AlphaAgent — Deployment Guide

## Architecture Overview

```
┌─────────────────────────────────┐          ┌───────────────────────────────────┐
│  Frontend (Vercel)              │          │  Backend (GCP VM - Docker)        │
│  https://alphaagent.skdev.one   │  ──────→ │  https://alphaagent-api.skdev.one │
│                                 │  HTTPS   │                                   │
│  Next.js 16 + React 19         │  API     │  FastAPI + uvicorn                │
│  Tailwind v4 + TypeScript      │  calls   │  Port 8030 (container: 8000)      │
│                                 │          │  Docker container: alphaagent-backend │
└─────────────────────────────────┘          │                                   │
                                             │  Also spawns:                     │
                                             │  run.py --session X --loop        │
                                             │  (agent processes inside container)│
                                             └───────────────────────────────────┘
```

| Component | URL | Hosted On |
|-----------|-----|-----------|
| Frontend | https://alphaagent.skdev.one | Vercel |
| Frontend (fallback) | https://alphaagent-xi.vercel.app | Vercel |
| Backend API | https://alphaagent-api.skdev.one | GCP e2-small VM |

---

## Prerequisites

### Local machine
- Node.js 18+ and npm
- Python 3.13+ with `.venv`
- Vercel CLI: `npx vercel` (available via npx, no global install needed)
- SSH access to GCP VM: `ssh ssh-social` (configured in `~/.ssh/config`)

### GCP VM (`ssh-social`)
- IP: `34.23.158.39`
- OS: Debian 12
- Docker 28.1+ and Docker Compose v2.3+
- Nginx (system-level, manages SSL for all `*.skdev.one` subdomains)
- Certbot (auto-renews Let's Encrypt certificates)

### DNS Records (Cloudflare or your DNS provider)
| Type | Name | Value |
|------|------|-------|
| CNAME | `alphaagent.skdev.one` | `cname.vercel-dns.com` |
| A | `alphaagent-api.skdev.one` | `34.23.158.39` |

### Environment Variables
The `.env` file at project root contains API keys:
```
OPENROUTER_API_KEY=sk-or-v1-xxxxx
```
This file is:
- **NOT** committed to git
- Copied to the VM during backend deploy
- Loaded by Docker via `env_file: .env` in docker-compose.yml

---

## Backend Deployment

### Quick deploy (one command)
```bash
cd /Users/rsumit123/work/ai-trader
bash deploy/deploy.sh
```

### What the deploy script does
1. Runs `docker system prune -f` on VM to free disk space
2. Creates `/home/rsumit123/alphaagent/` directories on VM
3. Rsyncs project files (excluding frontend, .venv, data dirs)
4. Copies `.env` file with API keys
5. Runs `docker compose up -d --build` on VM
6. Sets up nginx config if not already present

### Manual step-by-step deploy

#### 1. Sync files to VM
```bash
cd /Users/rsumit123/work/ai-trader

rsync -avz --delete \
  --exclude='.venv/' \
  --exclude='frontend/' \
  --exclude='node_modules/' \
  --exclude='sessions/' \
  --exclude='data/' \
  --exclude='learnings/' \
  --exclude='logs/' \
  --exclude='__pycache__/' \
  --exclude='.git/' \
  --exclude='.env.local' \
  ./ ssh-social:/home/rsumit123/alphaagent/
```

#### 2. Copy .env
```bash
scp .env ssh-social:/home/rsumit123/alphaagent/.env
```

#### 3. Build and start container
```bash
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose up -d --build"
```

#### 4. Verify
```bash
# Check container is running
ssh ssh-social "docker ps | grep alphaagent"

# Check API responds
curl -s https://alphaagent-api.skdev.one/api/sessions

# Check memory usage
ssh ssh-social "docker stats --no-stream alphaagent-backend"
```

### Docker details

**Dockerfile** — `python:3.13-slim` base with:
- `tini` as init (properly reaps zombie agent subprocesses)
- Single uvicorn worker (required: in-memory state + PID tracking)
- Health check: `curl -f http://localhost:8000/api/sessions`

**docker-compose.yml** — Key settings:
- Port mapping: `8030:8000` (host:container)
- Memory limit: `512m` (protects other containers on the VM)
- Swap limit: `768m`
- Stop grace period: `30s` (gives agents time to shut down)
- Restart policy: `unless-stopped`
- Volume mounts for persistent data:
  ```
  ./sessions:/app/sessions    # Session configs, DBs, journals, logs
  ./data:/app/data            # Legacy single-session data
  ./learnings:/app/learnings  # Legacy journal
  ./logs:/app/logs            # Legacy logs
  ```

**Important**: Session data lives OUTSIDE the container in volume mounts. Rebuilding the container does NOT lose any trading data.

### Nginx config

Located at `/etc/nginx/sites-enabled/alphaagent` on the VM.
Proxies `alphaagent-api.skdev.one` → `localhost:8030`.
SSL managed by Certbot (auto-renews).

First-time setup (already done):
```bash
ssh ssh-social
sudo cp /home/rsumit123/alphaagent/deploy/nginx-alphaagent.conf /etc/nginx/sites-enabled/alphaagent
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d alphaagent-api.skdev.one
```

### Useful backend commands

```bash
# View container logs
ssh ssh-social "docker logs alphaagent-backend --tail 50"

# Restart container (without rebuild)
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose restart"

# Full rebuild
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose up -d --build"

# Stop container
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose down"

# Check which sessions exist on VM
ssh ssh-social "ls /home/rsumit123/alphaagent/sessions/"

# Check disk usage
ssh ssh-social "df -h / && docker system df"

# Free disk space
ssh ssh-social "docker system prune -f"

# View agent logs for a specific session
ssh ssh-social "cat /home/rsumit123/alphaagent/sessions/crypto_test/agent.log | tail -50"

# Check if an agent process is running inside the container
ssh ssh-social "docker exec alphaagent-backend ps aux | grep run.py"
```

---

## Frontend Deployment

### Quick deploy
```bash
cd /Users/rsumit123/work/ai-trader/frontend
npx vercel --prod --yes
```

### What this does
1. Builds the Next.js app (uses `.env.production` for `NEXT_PUBLIC_API_URL`)
2. Uploads to Vercel
3. Deploys to production (https://alphaagent.skdev.one)

### Environment variables

**`.env.local`** (local dev — points to local FastAPI):
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**`.env.production`** (production build — points to GCP backend):
```
NEXT_PUBLIC_API_URL=https://alphaagent-api.skdev.one
```

The `NEXT_PUBLIC_API_URL` is baked into the JS bundle at build time. If you change the backend URL, you must redeploy the frontend.

### Vercel project details
- **Project name**: `alphaagent`
- **Team**: `rsumit123s-projects`
- **Framework**: Next.js (auto-detected)
- **Build command**: `npm run build`
- **Output directory**: `.next` (configured in `vercel.json`)
- **Custom domains**: `alphaagent.skdev.one`, `alphaagent-xi.vercel.app`
- **Project link**: `.vercel/` directory in `frontend/` (git-ignored)

### Local development

Terminal 1 — Backend:
```bash
cd /Users/rsumit123/work/ai-trader
set -a; source .env; set +a
.venv/bin/uvicorn dashboard.app:app --reload --port 8000
```

Terminal 2 — Frontend:
```bash
cd /Users/rsumit123/work/ai-trader/frontend
npm run dev
# Opens at http://localhost:3000 (or 3001 if 3000 is taken)
# API calls go to localhost:8000 via NEXT_PUBLIC_API_URL in .env.local
```

### Useful frontend commands

```bash
# Dev server
cd frontend && npm run dev

# Production build (test locally)
cd frontend && npm run build && npm run start

# Type check
cd frontend && npx tsc --noEmit

# Deploy to Vercel
cd frontend && npx vercel --prod --yes

# Check deployment status
npx vercel ls

# View deployment logs
npx vercel logs <deployment-url>
```

---

## CORS Configuration

The FastAPI backend (`dashboard/app.py`) allows these origins:
```python
allow_origins=[
    "http://localhost:3000",      # Next.js dev
    "http://localhost:3001",      # Next.js dev (alt port)
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
allow_origin_regex=r"https://.*\.vercel\.app"   # All Vercel preview deployments
```

If you deploy the frontend to a non-Vercel domain, add it to `allow_origins` in `dashboard/app.py` and redeploy the backend.

---

## Project Structure (deployment-relevant files)

```
ai-trader/
├── .env                        # API keys (NOT in git)
├── Dockerfile                  # Backend container definition
├── docker-compose.yml          # Container orchestration
├── .dockerignore               # Excludes frontend, .venv, data from image
├── requirements.txt            # Python dependencies
├── run.py                      # Agent entry point (runs inside container)
├── agent/                      # Python agent modules
├── dashboard/
│   ├── app.py                  # FastAPI backend (main API server)
│   └── index.html              # Legacy single-page dashboard (still served at /)
├── deploy/
│   ├── deploy.sh               # One-command backend deploy script
│   └── nginx-alphaagent.conf   # Nginx reverse proxy config
├── sessions/                   # Session data (volume-mounted, NOT in git)
│   ├── nse_default/
│   │   ├── config.yaml
│   │   ├── trades.db
│   │   ├── journal.md
│   │   └── agent.log
│   └── crypto_test/
│       └── ...
├── frontend/                   # Next.js app
│   ├── .env.local              # Local dev API URL
│   ├── .env.production         # Production API URL
│   ├── vercel.json             # Vercel framework config
│   ├── next.config.ts          # API rewrites for dev proxy
│   ├── package.json
│   └── src/
│       ├── app/                # Next.js pages (App Router)
│       ├── components/         # React components
│       └── lib/                # API client, types
└── scripts/
    └── migrate_to_sessions.py  # One-time migration from legacy paths
```

---

## Troubleshooting

### Backend API returns 502/503
```bash
# Check if container is running
ssh ssh-social "docker ps | grep alphaagent"

# Check container logs for errors
ssh ssh-social "docker logs alphaagent-backend --tail 100"

# Restart
ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose restart"
```

### Frontend shows "No sessions yet" but sessions exist
- Check browser console for CORS errors
- Verify `NEXT_PUBLIC_API_URL` is correct: should be `https://alphaagent-api.skdev.one` in production
- Test API directly: `curl https://alphaagent-api.skdev.one/api/sessions`

### Agent won't start from UI
- Check if `.env` on the VM has `OPENROUTER_API_KEY` set
- Check container logs: `ssh ssh-social "docker logs alphaagent-backend --tail 50"`
- The agent process runs INSIDE the container — check with:
  `ssh ssh-social "docker exec alphaagent-backend ps aux"`

### SSL certificate expired
```bash
ssh ssh-social "sudo certbot renew"
```
Certbot auto-renews via systemd timer, but you can force it.

### VM disk full
```bash
ssh ssh-social "df -h / && docker system prune -f"
```
The VM has only 10GB disk. Docker images and build cache fill up fast.

### Container uses too much memory
The `mem_limit: 512m` in docker-compose.yml prevents runaway memory. If the agent crashes with OOM:
- Reduce the watchlist size in the session config
- Run fewer concurrent sessions (1-2 max on e2-small)
- Check: `ssh ssh-social "docker stats --no-stream alphaagent-backend"`

---

## Iterative Development Workflow

### Typical change cycle

1. **Make changes locally** (agent code, API endpoints, or frontend components)

2. **Test locally**:
   ```bash
   # Backend
   .venv/bin/uvicorn dashboard.app:app --reload --port 8000

   # Frontend
   cd frontend && npm run dev
   ```

3. **Deploy what changed**:
   ```bash
   # Backend only (agent/dashboard code)
   bash deploy/deploy.sh

   # Frontend only (UI components/pages)
   cd frontend && npx vercel --prod --yes

   # Both
   bash deploy/deploy.sh && cd frontend && npx vercel --prod --yes
   ```

4. **Verify in production**:
   - Open https://alphaagent.skdev.one
   - Check API: `curl https://alphaagent-api.skdev.one/api/sessions`
