@AGENTS.md
@../CLAUDE.md

## Frontend-specific context

- **Stack**: Next.js 16 + React 19 + Tailwind v4 + TypeScript
- **API**: All data fetched client-side via `api()` from `src/lib/api.ts`
- **Styling**: Dark theme with custom colors in `@theme inline {}` in `globals.css`
- **Mobile**: Bottom nav (`BottomNav.tsx`), no hamburger menu. Top bar is logo + page title only.
- **Toasts**: Use `useToast()` from `src/components/Toast.tsx` for success/error feedback
- **Build**: `npm run build` must pass before deploying. Vercel auto-deploys on push to main.
