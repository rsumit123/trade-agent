# Custom Watchlist Categories — Design

**Date:** 2026-05-23
**Status:** Approved (pending spec review)
**Scope:** NSE equities only (v1)

## Problem

Users cannot control *what* the agent trades. Two pain points:

1. **No control over the universe.** A user who wants the agent to "trade only metals today," or to focus on a single stock like RELIANCE, has no way to express that.
2. **The current watchlist UI is confusing.** `WatchlistPanel.tsx` shows live prices for whatever tickers are in the watchlist, grouped by sector. For scanner-driven sessions (`nse-intraday`) that list is *silently replaced every morning* with the scanner's 25 picks — so the user sees 25 stocks they never chose, with no indication of why they are there or that they change daily. It reads like a price ticker, not a watchlist the user owns.

## Goal

Let users define the agent's trading universe — by sector category, by individual ticker, or both, down to a single stock — editable any time in settings, taking effect at the next market open. Replace the confusing watchlist panel with one that always makes the current state obvious.

## Core Model

**The universe is fundamentally a *set of tickers*. How that set is built determines the agent's mode, automatically:**

| Selection | Mode | Behavior |
|---|---|---|
| **"All sectors"** (default, empty selection) | **Discovery** | Pre-market scanner runs over the full ~3000-stock NSE universe and picks today's ~25 movers. This is today's existing behavior, unchanged. |
| **Any explicit pick** (one or more sectors and/or individual tickers — even a single stock) | **Fixed** | The picked set *is* the watchlist. The agent considers all of them every cycle. **The scanner does not run.** Nothing is pruned for "not moving today." |

Key consequences:

- **Categories are bulk-add shortcuts.** Selecting "Metals" drops its tickers into the set; the user can then remove individuals or add an arbitrary ticker via search.
- **Single-stock works trivially:** clear the set, add `RELIANCE.NS`. The agent watches only RELIANCE, always.
- **The scanner only ever runs in Discovery mode.** This eliminates the degenerate case where a hand-picked tiny universe gets filtered to zero candidates because nothing is gapping that day. Hand-picks are always honored.
- **Edits take effect at the next market open.** The selection is read fresh at the start of each trading day; saving mid-day does not disturb today's open positions.

## Data Model

### Backend: canonical sector map

Formalize the comment groups already present in `agent/market_presets.py` into a structured, authoritative map:

```python
# agent/market_presets.py
NSE_SECTORS: Dict[str, List[str]] = {
    "IT & Technology":  ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", ...],
    "Banking":          ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", ...],
    "Metals & Mining":  ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "COALINDIA.NS"],
    "Auto":             ["MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS", ...],
    # ... the ~25 groups that currently exist only as comments
}
```

The existing `NSE_PRESET.default_watchlist` becomes **derived** from this map (`[t for tickers in NSE_SECTORS.values() for t in tickers]`), so nothing changes when no selection is made. One source of truth; no duplicate ticker lists.

> **Note on `frontend/src/lib/sectors.ts`:** This is a separate, broader (~304-entry) map used **only to group the displayed watchlist into sections**. It is not authoritative for *what is tradeable per category* and may use slightly different names ("IT" vs "IT & Technology"). It stays as-is for display grouping. The category picker must NOT source from it — see API below.

### Session config

Add one field to `SessionConfig` (`agent/session.py`), included in `_YAML_FIELDS`:

```python
universe: Optional[List[str]] = None
# None or []  → Discovery mode (scanner over full universe) — the default.
# Non-empty   → Fixed mode: the agent's watchlist is exactly this list of tickers.
```

We store the resolved **ticker list**, not category names. Categories are a UI-side convenience that expands to tickers at selection time. This keeps the backend model simple (it only ever deals with a ticker set) and means a session is unaffected if a category's membership is later edited.

> The existing `watchlist` field semantics are preserved for Discovery mode and for non-NSE markets. In Fixed mode the effective watchlist is derived from `universe`.

## How It Plugs In

### Mode resolution (single helper)

A small resolver, e.g. `SessionConfig.resolve_universe()` (or a function in `market_presets.py`), returns `(mode, tickers)`:

- `universe` empty → `("discovery", None)`
- `universe` non-empty → `("fixed", universe)`

Both the runner and the scanner-gating logic call this one helper, so the rule lives in exactly one place.

### Scanner path (`agent/premarket_scanner.py`, `agent/runner.py`)

`_maybe_run_premarket_scan()` runs **only when mode is Discovery**. In Fixed mode the pre-market scan is skipped entirely and the watchlist is set to the resolved ticker list.

### Non-scanner path (`nse_default` and similar)

These never used the scanner. In Fixed mode the watchlist is the resolved ticker list. In Discovery mode (default), the behavior is unchanged (uses the derived full default watchlist). The watchlist is re-derived at the start of each trading day so a settings change is picked up next open.

### Daily pickup

The session config is already reloaded from disk each cycle (`_get_components` re-reads `SessionConfig`). The universe resolution happens at the day's first cycle / pre-market window, so edits made overnight take effect at the next open without restarting the agent.

### Non-NSE markets

Crypto and other markets ignore `universe`-as-categories for v1 (sectors are an equity concept). The field is still usable as a plain fixed ticker list if set, but the category UI is shown only for NSE. Crypto-specific categories (L1 / DeFi / Memes) are explicitly out of scope.

## API

New read-only endpoint to feed the picker from the authoritative backend map:

```
GET /api/categories?market=nse
→ { "categories": [ { "name": "Metals & Mining", "count": 5,
                      "tickers": ["TATASTEEL.NS", ...] }, ... ] }
```

The universe itself is saved through the **existing** `update_session` endpoint (`PATCH`/`POST` per current implementation) by adding `universe` to `UpdateSessionRequest`. No new write endpoint.

The session status payload (`/api/performance` or the session detail endpoint, wherever the watchlist is currently returned) gains:
- `universe_mode`: `"discovery" | "fixed"`
- `universe_count`: number of tickers (or `null`/`~3000` indicator for discovery)
- per watchlist item: `source`: `"scanner" | "pick"`

## UX

The user's explicit emphasis: **the user must clearly know what is going on.** Three always-visible signals.

### A. "Trading Universe" card in Settings (`sessions/[id]/settings`)

```
┌─ Trading Universe ─────────────────────────────────┐
│  Which stocks should the agent trade?               │
│                                                     │
│  ( • ) All sectors — agent scans the market daily   │
│  ( ) Choose specific sectors / stocks               │
│                                                     │
│  [ Metals & Mining ✓ ]  [ Auto ✓ ]  [ Banking + ]   │
│  [ IT & Technology + ]  [ Pharma + ]  [ FMCG + ] …  │
│                                                     │
│  + Add a specific stock:  [ search ticker…    ]     │
│                                                     │
│  In your universe (11):                             │
│   TATASTEEL · JSWSTEEL · HINDALCO · VEDL · …  [×]    │
│                                                     │
│  ⏱ Takes effect at next market open (Sat 9:15 IST). │
│     Today's open positions are unaffected.          │
│                       [ Save universe ]             │
└─────────────────────────────────────────────────────┘
```

- A clear two-way choice: **All sectors** (Discovery) vs **Choose specific** (Fixed).
- Category chips bulk-add/remove their tickers; an explicit "in your universe" list shows the resolved set with per-ticker remove.
- A ticker search adds individual stocks.
- The "takes effect" line computes the next market-open datetime in the session timezone.
- On save: confirmation toast — *"Saved. Starting at the next open (Sat 9:15 IST) the agent will trade: Metals, Auto (11 stocks). Today's positions are unaffected."*

### B. New Watchlist panel (replaces `WatchlistPanel.tsx`)

The old panel is removed. The replacement leads with context:

```
┌─ Watchlist ─────────────────────────────────────────┐
│  📡 Discovery — scanner picks today's movers          │   ← mode badge
│  (or)  📌 Fixed — your picks · 11 stocks   [⚙ Change] │
├──────────────────────────────────────────────────────┤
│  TATASTEEL   ▲ +1.2%   ₹142.5    📡 scanner pick      │
│  HINDALCO    ▼ -0.4%   ₹655.0    📌 your pick         │
│  …                                                    │
└──────────────────────────────────────────────────────┘
```

- **Mode badge** at the top states whether the list is scanner-discovered (dynamic, refreshes each open) or the user's fixed picks. This is the missing context that made the old panel confusing.
- **Per-row provenance tag** (`📡 scanner pick` / `📌 your pick`) from the `source` field.
- **`⚙ Change`** links straight to the Trading Universe card.
- Retains the useful parts of the old panel: live price/change and sector grouping toggle.

### C. Status line on the main session view

A compact banner so the user sees the mode without opening settings:

> 🎯 **Universe:** All NSE sectors (scanned daily) — *or* — **Metals, Auto** (11 stocks) · changes apply next open

## Error / Edge Handling

- **Empty selection in "Choose specific" mode:** the Save button is disabled with a hint ("Add at least one sector or stock, or switch to All sectors").
- **Invalid / unknown ticker in search:** validated against the cached NSE instrument list (`sessions/_nse_instruments.json`); rejected with a message if not found.
- **A picked ticker has no quote on a given day** (delisted/halted): it is shown in the watchlist with a "no data" state and simply skipped for trading that cycle; it is not silently removed from the user's saved universe.
- **Category membership changes later** (a ticker added/removed from `NSE_SECTORS`): existing sessions are unaffected because the resolved ticker list is stored, not the category name.

## Testing

- **Unit:** `resolve_universe()` returns `("discovery", None)` for empty/None and `("fixed", [...])` for a non-empty list; `NSE_SECTORS`-derived `default_watchlist` equals the previous hardcoded list (regression guard).
- **Unit:** `/api/categories` returns all sectors with correct counts; every ticker in every category exists in the instrument cache.
- **Integration:** a Fixed-mode session skips `_maybe_run_premarket_scan()` and sets the watchlist to exactly the saved universe; a Discovery-mode session still runs the scanner.
- **Integration:** editing the universe and advancing to the next simulated open changes the watchlist; mid-day edits do not.
- **Frontend:** category chips correctly add/remove tickers; single-stock selection produces a one-ticker universe; mode badge reflects discovery vs fixed.

## Out of Scope (v1)

- Crypto / non-NSE categories.
- Full-sector universes built by classifying *all* ~3000 NSE stocks (v1 uses the curated ~100-name category lists already in the preset).
- Per-stock weighting or capital allocation across the universe.
- Scheduling different universes for different days of the week.
