# AlphaAgent — Development Rules & Gotchas

Critical rules learned from production incidents. **Read before making any changes.**

---

## Frontend (Next.js + Tailwind v4)

### CSS: NEVER use unlayered global resets

Tailwind v4 puts all utility classes inside `@layer utilities`. The CSS Cascade spec says **unlayered CSS always beats layered CSS**. This means:

```css
/* WRONG — This silently kills ALL Tailwind spacing utilities */
* { margin: 0; padding: 0; box-sizing: border-box; }

/* RIGHT — Wrap in @layer base so Tailwind utilities can override */
@layer base {
  * { margin: 0; padding: 0; box-sizing: border-box; }
}
```

If you add any global CSS rule that touches `margin`, `padding`, `display`, `color`, etc. without wrapping it in `@layer base`, it will override every Tailwind class using those properties. The UI will look "flat" with no spacing — and there will be zero errors in the console.

### CSS: Turbopack caches by file path, not content

After editing `globals.css`, Turbopack may serve the stale cached version. The fix:
```bash
rm -rf .next && npm run dev
```
Always do this after any CSS changes if the browser doesn't reflect your edits.

### Tailwind v4 custom colors: Use inline styles for critical elements

The custom colors defined in `@theme inline {}` (like `bg-bg-secondary`, `text-text-primary`) may not reliably generate CSS classes in all contexts. For critical UI elements (sidebar, navigation, modals), use **inline styles with hardcoded hex values** as the primary approach, not Tailwind classes.

```tsx
// SAFE — always renders
<aside style={{ background: "#111827", borderRight: "1px solid #1e293b" }}>

// RISKY — may not generate the class
<aside className="bg-bg-secondary border-r border-border">
```

Tailwind classes are fine for layout utilities (`flex`, `grid`, `gap-4`, `rounded-xl`, etc.) — the issue is mainly with custom theme colors.

### Mobile touch targets: Minimum 44px

All interactive elements (buttons, links, inputs) must have `min-height: 44px` on mobile. This is Apple's Human Interface Guideline minimum. Inputs also need `font-size: 16px` to prevent iOS Safari auto-zoom on focus.

### OpenRouter model IDs differ from direct API model IDs

OpenRouter uses **short names** without date suffixes:
- OpenRouter: `anthropic/claude-sonnet-4-5`
- Direct Anthropic API: `claude-sonnet-4-5-20250929`

Always validate model IDs against the OpenRouter API before adding them to the UI.

---

## Backend (Python + FastAPI + Docker)

### Docker: Don't set memory limits below VM capacity

The container should use the full VM memory. Setting artificial limits (e.g., `mem_limit: 512m`) causes Docker OOM kills that silently kill agent subprocesses without any log entry.

### Agent subprocesses die on container restart

When Docker restarts the container, all child processes (spawned trading agents) die. The `_auto_restart_agents()` function in `dashboard/app.py` handles this by detecting stale lock files on startup. Don't remove this.

### Agent stderr goes to `agent_stderr.log`

Agent processes redirect stderr to `sessions/<id>/agent_stderr.log`. Check this file when an agent silently stops — it captures Python tracebacks that `agent.log` might miss.

### 24/7 markets (crypto) need explicit daily review triggers

For markets with no close time, `run_daily_review()` must be triggered via `_maybe_run_daily_review()` on a daily UTC schedule. Without this, the learning pipeline (distilled rules) never runs.

---

## Deployment

### Frontend: Vercel auto-deploys from Git

- Root directory is set to `frontend/` in Vercel project settings
- `NEXT_PUBLIC_API_URL` env var is set in Vercel (points to `https://alphaagent-api.skdev.one`)
- Every push to `main` triggers auto-deploy — no manual `vercel --prod` needed

### Backend: Manual deploy via rsync + Docker

```bash
# From repo root:
rsync -avz --delete \
  --exclude='.venv/' --exclude='frontend/' --exclude='node_modules/' \
  --exclude='sessions/' --exclude='data/' --exclude='learnings/' --exclude='logs/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='.env.local' \
  ./ ssh-social:/home/rsumit123/alphaagent/

ssh ssh-social "cd /home/rsumit123/alphaagent && docker compose up -d --build --force-recreate"
```

**Never sync `sessions/` to production** — that would overwrite live trade data.

### CORS: Custom domains must be explicitly allowed

The FastAPI CORS middleware only allows origins that match the `allow_origins` list or `allow_origin_regex`. If you add a new custom domain, add it to `dashboard/app.py` CORS config and redeploy the backend.

### Monitoring

A cron job runs every 5 minutes on the VM (`deploy/monitor.sh`) and sends Telegram alerts when RAM > 85%, Swap > 80%, Disk > 90%, or container is down. Rate-limited to 1 alert per hour.
