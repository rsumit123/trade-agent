# Custom Watchlist Categories — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users define the agent's trading universe — by official NSE sector category, by individual ticker, or both (down to a single stock) — editable in settings and applied at the next market open, with a watchlist UI that always makes the current mode obvious.

**Architecture:** A new `agent/sector_data.py` fetches official NIFTY sectoral-index constituents and caches them (`sessions/_nse_sectors.json`, monthly refresh). `SessionConfig` gains a `universe` field (a resolved ticker list); empty = **Discovery** mode (scanner runs, today's behavior), non-empty = **Fixed** mode (that exact list is the watchlist, scanner off). The runner gates the pre-market scan on this. A `GET /api/categories` endpoint feeds a settings picker; the watchlist panel is rewritten to show mode + per-ticker provenance.

**Tech Stack:** Python 3.13 + FastAPI (backend), Next.js 16 + React 19 + Tailwind v4 + TypeScript (frontend). Tests: pytest (bootstrapped in Task 0) for backend; `npm run build` + manual verification for frontend.

---

## File Structure

**Backend**
- Create `agent/sector_data.py` — NIFTY index CSV fetch, parse, cache, `get_nse_sectors()`.
- Modify `agent/session.py` — add `universe` field, add to `_YAML_FIELDS`, add `resolve_universe()`.
- Modify `agent/runner.py:858` (`_maybe_run_premarket_scan`) — Fixed mode sets watchlist from universe and skips scan.
- Modify `dashboard/app.py` — `GET /api/categories`, `universe` in `UpdateSessionRequest` + `update_session`, mode/source fields in the session status payload.

**Frontend**
- Modify `frontend/src/lib/types.ts` — add `universe`, `universe_mode`, `universe_count`, item `source`, `Category` type.
- Modify `frontend/src/lib/api.ts` — `getCategories()`, `updateUniverse()` helpers.
- Create `frontend/src/components/TradingUniverseCard.tsx` — the picker.
- Rewrite `frontend/src/components/WatchlistPanel.tsx` — mode badge + provenance + Change link.
- Modify `frontend/src/app/app/sessions/[id]/settings/page.tsx` — render `TradingUniverseCard`.

**Tests**
- Create `tests/test_sector_data.py`, `tests/test_universe_config.py`, `tests/test_categories_api.py`.

---

## Task 0: Bootstrap pytest

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/` (directory)

- [ ] **Step 1: Install pytest into the venv**

Run: `.venv/bin/pip install pytest`
Expected: ends with `Successfully installed ... pytest-<version>`

- [ ] **Step 2: Record the dev dependency**

Create `requirements-dev.txt`:

```
pytest>=8.0
```

- [ ] **Step 3: Create the tests directory with a smoke test**

Create `tests/test_smoke.py`:

```python
def test_smoke():
    assert True
```

- [ ] **Step 4: Run pytest from the repo root to verify discovery + imports work**

Run: `.venv/bin/python -m pytest tests/test_smoke.py -v`
Expected: PASS (1 passed). (Running from repo root makes the `agent` package importable.)

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt tests/test_smoke.py
git commit -m "test: bootstrap pytest harness"
```

---

## Task 1: NSE sector fetcher + cache (`agent/sector_data.py`)

**Files:**
- Create: `agent/sector_data.py`
- Test: `tests/test_sector_data.py`

The cache mirrors the existing instrument-cache pattern in `agent/premarket_scanner.py:73` (JSON file with a timestamp field, staleness check, graceful failure).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sector_data.py`:

```python
import json
from pathlib import Path
import agent.sector_data as sd


SAMPLE_CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Tata Steel Limited,Metals & Mining,TATASTEEL,EQ,INE081A01020\n"
    "JSW Steel Limited,Metals & Mining,JSWSTEEL,EQ,INE019A01038\n"
)


def test_parse_csv_extracts_ns_tickers():
    assert sd.parse_constituents_csv(SAMPLE_CSV) == ["TATASTEEL.NS", "JSWSTEEL.NS"]


def test_parse_csv_ignores_blank_and_malformed_rows():
    csv = SAMPLE_CSV + "\n,,,,\nGarbage line without commas\n"
    assert sd.parse_constituents_csv(csv) == ["TATASTEEL.NS", "JSWSTEEL.NS"]


def test_load_cache_returns_sectors_when_fresh(tmp_path, monkeypatch):
    cache = tmp_path / "_nse_sectors.json"
    cache.write_text(json.dumps({
        "fetched_at": "2999-01-01T00:00:00",  # far future → never stale
        "sectors": {"Metals": ["TATASTEEL.NS"]},
    }))
    monkeypatch.setattr(sd, "SECTOR_CACHE", cache)
    # Force-fail the network path so we know the cache was used
    monkeypatch.setattr(sd, "_refresh_sectors", lambda: {})
    assert sd.get_nse_sectors() == {"Metals": ["TATASTEEL.NS"]}


def test_refresh_failure_falls_back_to_stale_cache(tmp_path, monkeypatch):
    cache = tmp_path / "_nse_sectors.json"
    cache.write_text(json.dumps({
        "fetched_at": "2000-01-01T00:00:00",  # stale → triggers refresh
        "sectors": {"Metals": ["TATASTEEL.NS"]},
    }))
    monkeypatch.setattr(sd, "SECTOR_CACHE", cache)
    monkeypatch.setattr(sd, "_fetch_index_csv", lambda slug: None)  # all fetches fail
    # stale cache exists → returned despite refresh producing nothing usable
    assert sd.get_nse_sectors() == {"Metals": ["TATASTEEL.NS"]}


def test_no_cache_and_failed_fetch_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SECTOR_CACHE", tmp_path / "_nse_sectors.json")
    monkeypatch.setattr(sd, "_fetch_index_csv", lambda slug: None)
    assert sd.get_nse_sectors() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sector_data.py -v`
Expected: FAIL (ModuleNotFoundError / AttributeError — `agent.sector_data` not implemented).

- [ ] **Step 3: Implement `agent/sector_data.py`**

Create `agent/sector_data.py`:

```python
"""
NSE sectoral-index constituents → category map.

Sources official NIFTY sector index constituent CSVs from niftyindices.com,
caches them to sessions/_nse_sectors.json, and refreshes monthly. A stale
cache is always preferred over a failed fetch. The agent's Discovery-mode
default does NOT depend on this — it is only consulted for the category picker.
"""

import csv as _csv
import io
import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SECTOR_CACHE = Path("sessions/_nse_sectors.json")
CACHE_MAX_AGE_DAYS = 30

# Display name → niftyindices CSV slug.
NSE_SECTOR_INDICES = {
    "Metals":             "ind_niftymetallist",
    "Auto":               "ind_niftyautolist",
    "IT":                 "ind_niftyitlist",
    "Bank":               "ind_niftybanklist",
    "Pharma":             "ind_niftypharmalist",
    "FMCG":               "ind_niftyfmcglist",
    "Energy":             "ind_niftyenergylist",
    "Realty":             "ind_niftyrealtylist",
    "Media":              "ind_niftymedialist",
    "Financial Services": "ind_niftyfinservicelist",
    "Oil & Gas":          "ind_niftyoilgaslist",
    "Healthcare":         "ind_niftyhealthcarelist",
    "Consumer Durables":  "ind_niftyconsumerdurableslist",
    "PSU Bank":           "ind_niftypsubanklist",
    "Private Bank":       "ind_niftyprivatebanklist",
}

_CSV_BASE = "https://niftyindices.com/IndexConstituent/{slug}.csv"
_UA = "Mozilla/5.0 (compatible; AlphaAgent/1.0)"


def parse_constituents_csv(text: str) -> List[str]:
    """Parse a NIFTY constituent CSV into ['TATASTEEL.NS', ...]."""
    out: List[str] = []
    reader = _csv.DictReader(io.StringIO(text))
    for row in reader:
        sym = (row.get("Symbol") or "").strip()
        if sym:
            out.append(f"{sym}.NS")
    return out


def _fetch_index_csv(slug: str) -> Optional[str]:
    """Download one index constituent CSV. Returns text or None on failure."""
    url = _CSV_BASE.format(slug=slug)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"Sector fetch failed for {slug}: {e}")
        return None


def _refresh_sectors() -> Dict[str, List[str]]:
    """Fetch all tracked indices. Returns {sector: [tickers]} for those that succeeded."""
    sectors: Dict[str, List[str]] = {}
    for name, slug in NSE_SECTOR_INDICES.items():
        text = _fetch_index_csv(slug)
        if not text:
            continue
        tickers = parse_constituents_csv(text)
        if tickers:
            sectors[name] = tickers
    return sectors


def _read_cache() -> Optional[dict]:
    if not SECTOR_CACHE.exists():
        return None
    try:
        return json.loads(SECTOR_CACHE.read_text())
    except Exception:
        return None


def get_nse_sectors() -> Dict[str, List[str]]:
    """
    Return {sector_name: [.NS tickers]}.
    Uses the cache when fresh; refreshes when stale/missing; falls back to a
    stale cache if the refresh yields nothing; returns {} only when there is
    no cache and the fetch fails.
    """
    cached = _read_cache()
    if cached:
        try:
            age = (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).days
            if age < CACHE_MAX_AGE_DAYS and cached.get("sectors"):
                return cached["sectors"]
        except Exception:
            pass

    fresh = _refresh_sectors()
    if fresh:
        SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_CACHE.write_text(json.dumps(
            {"fetched_at": datetime.now().isoformat(), "sectors": fresh}, indent=2
        ))
        return fresh

    # Refresh failed — prefer a stale cache over nothing.
    if cached and cached.get("sectors"):
        logger.warning("Sector refresh failed; using stale cache.")
        return cached["sectors"]
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sector_data.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/sector_data.py tests/test_sector_data.py
git commit -m "feat: NSE sectoral-index constituent fetcher + cache"
```

---

## Task 2: `universe` config field + `resolve_universe()`

**Files:**
- Modify: `agent/session.py` (SessionConfig dataclass, `_YAML_FIELDS` at line ~152)
- Test: `tests/test_universe_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_universe_config.py`:

```python
from agent.session import SessionConfig


def test_default_universe_is_discovery():
    sc = SessionConfig(session_id="t", market="nse")
    assert sc.resolve_universe() == ("discovery", None)


def test_nonempty_universe_is_fixed():
    sc = SessionConfig(session_id="t", market="nse", universe=["RELIANCE.NS"])
    assert sc.resolve_universe() == ("fixed", ["RELIANCE.NS"])


def test_empty_list_universe_is_discovery():
    sc = SessionConfig(session_id="t", market="nse", universe=[])
    assert sc.resolve_universe() == ("discovery", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_universe_config.py -v`
Expected: FAIL (`TypeError: unexpected keyword 'universe'` or `AttributeError: resolve_universe`).

- [ ] **Step 3: Add the field**

In `agent/session.py`, in the `SessionConfig` dataclass next to `watchlist` (line ~48), add:

```python
    universe: Optional[List[str]] = None        # None/[] → Discovery; non-empty → Fixed watchlist
```

- [ ] **Step 4: Add the resolver method**

In `agent/session.py`, add this method to `SessionConfig` (e.g. after the defaults-resolution method near line ~131):

```python
    def resolve_universe(self):
        """Return ('discovery', None) when no explicit picks, else ('fixed', [tickers])."""
        if self.universe:
            return ("fixed", list(self.universe))
        return ("discovery", None)
```

- [ ] **Step 5: Persist the field**

In `agent/session.py`, add `"universe"` to the `_YAML_FIELDS` set (line ~152), on the line with `"watchlist"`:

```python
    "watchlist", "universe", "llm_provider", "llm_model", "api_key_env",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_universe_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add agent/session.py tests/test_universe_config.py
git commit -m "feat: add universe field + resolve_universe to SessionConfig"
```

---

## Task 3: Runner gates the scanner on universe mode

**Files:**
- Modify: `agent/runner.py:858` (`_maybe_run_premarket_scan`)

This is integration glue around `SessionConfig.resolve_universe()` (already unit-tested). Verification is via reading the log on a real cycle, not a unit test (the runner needs live market data + Kite to construct).

**Two facts that shape this task** (verified against `agent/runner.py:34-44`):
- `self.config` is an `AgentConfig` (built via `AgentConfig.from_session`) and does **not** have `resolve_universe()`. The `SessionConfig` is stored as `self.session` (may be `None` for the legacy non-session path). The agent's effective watchlist lives on `self.config.watchlist` (AgentConfig) and `self.market_data.watchlist` — the existing scanner code at line ~904 sets exactly those.
- The agent runs as a long-lived process holding `self.session` in memory, so an overnight settings edit must be **re-read from disk** at the day boundary for "takes effect next open" to hold without a restart. The per-day guard (`_scan_date == today`) means the inserted block runs once per calendar day — the right place to reload.

- [ ] **Step 1: Insert the Fixed-mode short-circuit (with daily disk reload)**

In `agent/runner.py`, at the top of `_maybe_run_premarket_scan` — immediately after the per-day guard `if self._scan_date == today and self._scan_done_today: return` (line ~866-867) and before the `use_scanner = (...)` check (line ~870) — add:

```python
        # New trading day: reload session config from disk so overnight
        # universe edits take effect at the next open without a restart.
        if self.session is not None:
            try:
                from .session import load_session
                fresh = load_session(self.session.session_id)
                self.session.universe = fresh.universe
            except Exception as e:
                logger.warning(f"Universe reload failed, keeping in-memory value: {e}")

            # Fixed universe → use the user's exact picks; never run the scanner.
            mode, tickers = self.session.resolve_universe()
            if mode == "fixed":
                self.config.watchlist = list(tickers)
                self.market_data.watchlist = list(tickers)
                self._premarket_summary = (
                    f"## Trading Universe — {len(tickers)} stocks chosen by the user\n"
                    + "\n".join(f"  - {t}" for t in tickers)
                )
                self._scan_done_today = True
                self._scan_date = today
                logger.info(f"📌 Fixed universe: trading {len(tickers)} user-selected stocks (scanner skipped)")
                return
```

- [ ] **Step 2: Verify Python imports cleanly**

Run: `.venv/bin/python -c "import agent.runner"`
Expected: no output, exit 0.

- [ ] **Step 3: Verify the gating logic with a stub**

Run:
```bash
.venv/bin/python -c "
from agent.session import SessionConfig
sc = SessionConfig(session_id='t', market='nse', universe=['RELIANCE.NS'])
print(sc.resolve_universe())
sc2 = SessionConfig(session_id='t', market='nse')
print(sc2.resolve_universe())
"
```
Expected:
```
('fixed', ['RELIANCE.NS'])
('discovery', None)
```

- [ ] **Step 4: Commit**

```bash
git add agent/runner.py
git commit -m "feat: skip pre-market scanner when universe is fixed"
```

---

## Task 4: `GET /api/categories` endpoint

**Files:**
- Modify: `dashboard/app.py`
- Test: `tests/test_categories_api.py`

Test the pure payload builder directly (no FastAPI TestClient dependency).

- [ ] **Step 1: Write the failing test**

Create `tests/test_categories_api.py`:

```python
import dashboard.app as app


def test_categories_payload_shape(monkeypatch):
    monkeypatch.setattr(
        app, "get_nse_sectors",
        lambda: {"Metals": ["TATASTEEL.NS", "JSWSTEEL.NS"], "Auto": ["MARUTI.NS"]},
    )
    payload = app._categories_payload("nse")
    assert payload["source"] == "nse_sectoral_indices"
    names = {c["name"]: c for c in payload["categories"]}
    assert names["Metals"]["count"] == 2
    assert names["Metals"]["tickers"] == ["TATASTEEL.NS", "JSWSTEEL.NS"]
    assert names["Auto"]["count"] == 1


def test_categories_payload_non_nse_is_empty(monkeypatch):
    monkeypatch.setattr(app, "get_nse_sectors", lambda: {"Metals": ["TATASTEEL.NS"]})
    payload = app._categories_payload("crypto")
    assert payload["categories"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_categories_api.py -v`
Expected: FAIL (`AttributeError: _categories_payload`).

- [ ] **Step 3: Implement the builder + route**

In `dashboard/app.py`, add the import near the other `agent` imports at the top:

```python
from agent.sector_data import get_nse_sectors
```

Add the payload builder (near other helpers, e.g. after the CORS/setup block):

```python
def _categories_payload(market: str) -> dict:
    """Category list for the universe picker. Sectors apply to NSE only."""
    if market != "nse":
        return {"source": "nse_sectoral_indices", "categories": []}
    sectors = get_nse_sectors()
    return {
        "source": "nse_sectoral_indices",
        "categories": [
            {"name": name, "count": len(tickers), "tickers": tickers}
            for name, tickers in sectors.items()
        ],
    }
```

Add the route (near the other `@app.get` session/config routes):

```python
@app.get("/api/categories")
def get_categories(market: str = "nse"):
    """Sector categories for building a trading universe."""
    return _categories_payload(market)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_categories_api.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py tests/test_categories_api.py
git commit -m "feat: GET /api/categories endpoint"
```

---

## Task 5: Persist `universe` via `update_session`

**Files:**
- Modify: `dashboard/app.py` (`UpdateSessionRequest` at line ~513, `update_session` at line ~1144)
- Test: extend `tests/test_universe_config.py`

- [ ] **Step 1: Write the failing test (config round-trip)**

Append to `tests/test_universe_config.py`:

```python
def test_universe_survives_yaml_round_trip(tmp_path, monkeypatch):
    import agent.session as session_mod
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    (tmp_path / "t").mkdir()
    sc = SessionConfig(session_id="t", market="nse", universe=["RELIANCE.NS", "TCS.NS"])
    session_mod.save_session(sc)
    loaded = session_mod.load_session("t")
    assert loaded.universe == ["RELIANCE.NS", "TCS.NS"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_universe_config.py::test_universe_survives_yaml_round_trip -v`
Expected: FAIL if `save_session`/`SESSIONS_DIR` names differ — if so, adjust the test to the actual save function name found in `agent/session.py` (grep `def save`), then proceed. (The `universe` field was added to `_YAML_FIELDS` in Task 2, so the round-trip should work once names match.)

- [ ] **Step 3: Add `universe` to the request model**

In `dashboard/app.py`, in `UpdateSessionRequest` (line ~513), add:

```python
    universe: Optional[List[str]] = None
```

- [ ] **Step 4: Apply it in `update_session`**

In `dashboard/app.py`, inside `update_session` (line ~1144), where other fields are copied from `req` onto the loaded `SessionConfig`, add:

```python
    if req.universe is not None:
        sc.universe = req.universe
```

Place it alongside the existing `if req.watchlist is not None:` style assignments, before the session is saved.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_universe_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py tests/test_universe_config.py
git commit -m "feat: persist universe through update_session"
```

---

## Task 6: Mode + provenance in the session status payload

**Files:**
- Modify: `dashboard/app.py` (the watchlist/status payload near line ~2006–2034)

The payload currently returns `watchlist` items and `watchlist_count`. Add `universe_mode`, `universe_count`, and a `source` tag per item.

**Fact (verified at `dashboard/app.py:1995`):** in this scope `ac = c["config"]` is the **AgentConfig** (no `resolve_universe`), while `sc` is the **SessionConfig** (used on the next lines as `sc.llm_model`). Call `resolve_universe()` on **`sc`**.

- [ ] **Step 1: Locate the payload assembly**

Run: `grep -n '"watchlist_count"\|"watchlist":' dashboard/app.py`
Expected: lines around 2006 and 2034 (the status/performance payload).

- [ ] **Step 2: Compute mode + tag items**

In `dashboard/app.py`, immediately before the payload dict that contains `"watchlist": watchlist_data ...` is built, add:

```python
    _u_mode, _u_tickers = sc.resolve_universe()
    _picked = set(_u_tickers or [])
    if isinstance(watchlist_data, list):
        for _it in watchlist_data:
            if isinstance(_it, dict):
                _it["source"] = "pick" if _it.get("ticker") in _picked else "scanner"
```

(`sc` is the active `SessionConfig` in scope; `ac` next to `"watchlist_count": len(ac.watchlist)` is the AgentConfig.)

- [ ] **Step 3: Add the mode fields to the payload**

In the same payload dict (where `"watchlist_count"` is set, line ~2006), add:

```python
        "universe_mode": _u_mode,
        "universe_count": (len(_u_tickers) if _u_tickers else None),
```

- [ ] **Step 4: Verify import + syntax**

Run: `.venv/bin/python -c "import dashboard.app"`
Expected: no output, exit 0.

- [ ] **Step 5: Run the full backend test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: expose universe_mode + per-item source in status payload"
```

---

## Task 7: Frontend types + API helpers

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add types**

In `frontend/src/lib/types.ts`, add:

```ts
export type Category = { name: string; count: number; tickers: string[] };
```

Add to the `SessionConfig` type: `universe?: string[] | null;`
Add to the watchlist/status response type (where `watchlist` and `watchlist_count` live): `universe_mode?: "discovery" | "fixed"; universe_count?: number | null;`
Add to the `WatchlistItem` type: `source?: "scanner" | "pick";`

- [ ] **Step 2: Add API helpers**

In `frontend/src/lib/api.ts`, following the existing fetch-helper style (use the same base-URL + auth-header pattern as the other calls in this file), add:

```ts
export async function getCategories(market = "nse"): Promise<{ source: string; categories: Category[] }> {
  return apiGet(`/api/categories?market=${encodeURIComponent(market)}`);
}

export async function updateUniverse(sessionId: string, universe: string[]): Promise<void> {
  await apiPatch(`/api/sessions/${sessionId}`, { universe });
}
```

Match the actual helper names in this file (e.g. if calls use `api.get`/`apiFetch` rather than `apiGet`/`apiPatch`, use those). Import `Category` from `./types`.

- [ ] **Step 3: Verify the frontend type-checks**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TS errors). If `apiGet`/`apiPatch` names were wrong, the build error names the real export — fix and rebuild.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(fe): universe types + category/universe API helpers"
```

---

## Task 8: `TradingUniverseCard` settings component

**Files:**
- Create: `frontend/src/components/TradingUniverseCard.tsx`
- Modify: `frontend/src/app/app/sessions/[id]/settings/page.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/TradingUniverseCard.tsx`:

```tsx
"use client";
import { useEffect, useMemo, useState } from "react";
import { getCategories, updateUniverse } from "@/lib/api";
import type { Category, SessionConfig } from "@/lib/types";

export function TradingUniverseCard({
  sessionId,
  config,
  onSaved,
}: {
  sessionId: string;
  config: SessionConfig | null;
  onSaved?: () => void;
}) {
  const [cats, setCats] = useState<Category[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set(config?.universe ?? []));
  const [mode, setMode] = useState<"all" | "specific">(
    (config?.universe?.length ?? 0) > 0 ? "specific" : "all",
  );
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");

  useEffect(() => {
    getCategories(config?.market || "nse").then((r) => setCats(r.categories)).catch(() => setCats([]));
  }, [config?.market]);

  const tickersOf = (name: string) => cats.find((c) => c.name === name)?.tickers ?? [];
  const sectorFullySelected = (name: string) =>
    tickersOf(name).length > 0 && tickersOf(name).every((t) => selected.has(t));

  const toggleSector = (name: string) => {
    const next = new Set(selected);
    const tks = tickersOf(name);
    if (sectorFullySelected(name)) tks.forEach((t) => next.delete(t));
    else tks.forEach((t) => next.add(t));
    setSelected(next);
  };

  const addTicker = (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!t) return;
    const ticker = t.endsWith(".NS") ? t : `${t}.NS`;
    setSelected(new Set(selected).add(ticker));
    setSearch("");
  };

  const removeTicker = (t: string) => {
    const next = new Set(selected);
    next.delete(t);
    setSelected(next);
  };

  const list = useMemo(() => Array.from(selected), [selected]);
  const canSave = mode === "all" || list.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await updateUniverse(sessionId, mode === "all" ? [] : list);
      setSavedMsg(
        mode === "all"
          ? "Saved. The agent will scan all NSE sectors at the next open."
          : `Saved. Starting next open the agent will trade ${list.length} stock${list.length === 1 ? "" : "s"}. Today's positions are unaffected.`,
      );
      onSaved?.();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-bg-card border border-border rounded-xl p-4" style={{ background: "#0c1320", border: "1px solid #1e293b" }}>
      <h3 className="text-sm font-semibold text-text-primary mb-1">Trading Universe</h3>
      <p className="text-xs text-text-muted mb-3">Which stocks should the agent trade?</p>

      <div className="flex flex-col gap-2 mb-3">
        <label className="flex items-center gap-2 text-sm text-text-primary">
          <input type="radio" checked={mode === "all"} onChange={() => setMode("all")} />
          All sectors — agent scans the market daily
        </label>
        <label className="flex items-center gap-2 text-sm text-text-primary">
          <input type="radio" checked={mode === "specific"} onChange={() => setMode("specific")} />
          Choose specific sectors / stocks
        </label>
      </div>

      {mode === "specific" && (
        <>
          <div className="flex flex-wrap gap-2 mb-3">
            {cats.map((c) => {
              const on = sectorFullySelected(c.name);
              return (
                <button
                  key={c.name}
                  onClick={() => toggleSector(c.name)}
                  className="text-xs px-2 py-1 rounded-lg border"
                  style={{
                    minHeight: 44,
                    background: on ? "#1d4ed8" : "#0a0e17",
                    borderColor: on ? "#3b82f6" : "#1e293b",
                    color: on ? "#fff" : "#94a3b8",
                  }}
                >
                  {c.name} ({c.count}) {on ? "✓" : "+"}
                </button>
              );
            })}
          </div>

          <div className="flex gap-2 mb-3">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addTicker(search)}
              placeholder="Add a stock (e.g. RELIANCE)"
              className="flex-1 text-sm px-2 rounded-lg"
              style={{ minHeight: 44, fontSize: 16, background: "#0a0e17", border: "1px solid #1e293b", color: "#e2e8f0" }}
            />
            <button onClick={() => addTicker(search)} className="text-sm px-3 rounded-lg" style={{ minHeight: 44, background: "#1e293b", color: "#e2e8f0" }}>
              Add
            </button>
          </div>

          <div className="mb-3">
            <div className="text-xs text-text-muted mb-1">In your universe ({list.length}):</div>
            <div className="flex flex-wrap gap-1.5">
              {list.map((t) => (
                <span key={t} className="text-xs px-2 py-1 rounded-md flex items-center gap-1" style={{ background: "#0a0e17", border: "1px solid #1e293b", color: "#e2e8f0" }}>
                  {t.replace(".NS", "")}
                  <button onClick={() => removeTicker(t)} className="opacity-60 hover:opacity-100">×</button>
                </span>
              ))}
              {list.length === 0 && <span className="text-xs text-text-muted">No stocks yet — add a sector or a ticker.</span>}
            </div>
          </div>
        </>
      )}

      <p className="text-xs text-text-muted mb-2">⏱ Takes effect at the next market open. Today's open positions are unaffected.</p>
      <button
        onClick={save}
        disabled={!canSave || saving}
        className="text-sm px-4 rounded-lg font-medium"
        style={{ minHeight: 44, background: canSave ? "#1d4ed8" : "#1e293b", color: "#fff", opacity: saving ? 0.6 : 1 }}
      >
        {saving ? "Saving…" : "Save universe"}
      </button>
      {savedMsg && <p className="text-xs mt-2" style={{ color: "#22c55e" }}>{savedMsg}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Render it in settings**

In `frontend/src/app/app/sessions/[id]/settings/page.tsx`, import and render the card within the settings layout (pass the session id and the loaded config; follow how the page already obtains `sessionId` and `config`):

```tsx
import { TradingUniverseCard } from "@/components/TradingUniverseCard";
// ...inside the rendered settings sections:
<TradingUniverseCard sessionId={sessionId} config={config} />
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual verification**

Run `cd frontend && npm run dev`, open a session's settings, confirm: sectors load as chips; selecting a sector populates "In your universe"; adding a ticker works; "All sectors" disables the picker; Save shows the confirmation message.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TradingUniverseCard.tsx "frontend/src/app/app/sessions/[id]/settings/page.tsx"
git commit -m "feat(fe): Trading Universe picker in settings"
```

---

## Task 9: Rewrite the Watchlist panel

**Files:**
- Modify (rewrite): `frontend/src/components/WatchlistPanel.tsx`

Keep live price/change + sector grouping, but lead with a mode badge and tag each row's provenance. The component now also receives `mode`/`count` and links to settings.

- [ ] **Step 1: Update the props + header**

Change the `WatchlistPanel` signature to accept the mode and a settings link:

```tsx
export function WatchlistPanel({
  items,
  config,
  mode,
  count,
  onChangeUniverse,
}: {
  items: WatchlistItem[];
  config: SessionConfig | null;
  mode?: "discovery" | "fixed";
  count?: number | null;
  onChangeUniverse?: () => void;
}) {
```

Replace the existing header block (the `<div className="flex items-center justify-between px-4 py-3 ...">` containing the "Watchlist" title) with a header that adds the mode badge and a Change button:

```tsx
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Watchlist</h3>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5 rounded-lg p-0.5" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
              <ToggleBtn active={view === "sector"} onClick={() => setView("sector")}>Sector</ToggleBtn>
              <ToggleBtn active={view === "flat"} onClick={() => setView("flat")}>Flat</ToggleBtn>
            </div>
            <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{items.length}</span>
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-[11px]" style={{ color: mode === "fixed" ? "#60a5fa" : "#22c55e" }}>
            {mode === "fixed"
              ? `📌 Fixed — your picks${count ? ` · ${count} stocks` : ""}`
              : "📡 Discovery — scanner picks today's movers, refreshes each open"}
          </span>
          {onChangeUniverse && (
            <button onClick={onChangeUniverse} className="text-[11px] underline text-text-muted hover:text-text-primary">⚙ Change</button>
          )}
        </div>
      </div>
```

- [ ] **Step 2: Tag each row's provenance in `TickerRow`**

In the `TickerRow` component within this file, append a small provenance label using `item.source`:

```tsx
        {item.source && (
          <span className="text-[10px] ml-2" style={{ color: item.source === "pick" ? "#60a5fa" : "#94a3b8" }}>
            {item.source === "pick" ? "📌 your pick" : "📡 scanner"}
          </span>
        )}
```

Place it inside the row's right-hand cluster (next to price/change) so it reads inline. If `TickerRow` doesn't already receive the full `item`, it does (`item: WatchlistItem`) — use `item.source`.

- [ ] **Step 3: Pass the new props where the panel is rendered**

In `frontend/src/app/app/sessions/[id]/page.tsx` (where `<WatchlistPanel ... />` is used), pass `mode`, `count`, and a handler that routes to settings:

```tsx
<WatchlistPanel
  items={status?.watchlist ?? []}
  config={config}
  mode={status?.universe_mode}
  count={status?.universe_count}
  onChangeUniverse={() => router.push(`/app/sessions/${sessionId}/settings`)}
/>
```

Use the page's existing `status`/`config`/`router`/`sessionId` references (match their actual names — grep the file).

- [ ] **Step 4: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Manual verification**

In `npm run dev`: a Discovery session shows the green `📡 Discovery` badge; a Fixed session shows the blue `📌 Fixed · N stocks` badge; rows show `📡 scanner` / `📌 your pick`; the ⚙ Change button navigates to settings.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/WatchlistPanel.tsx "frontend/src/app/app/sessions/[id]/page.tsx"
git commit -m "feat(fe): self-explanatory watchlist panel with mode badge + provenance"
```

---

## Task 10: Final verification

- [ ] **Step 1: Full backend test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 2: Backend import smoke**

Run: `.venv/bin/python -c "import dashboard.app, agent.runner, agent.sector_data, agent.session; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds, no type errors.

- [ ] **Step 4: End-to-end smoke (local, optional but recommended)**

Start the backend locally, create/edit a session, set a single-stock universe (`RELIANCE`), confirm `config.yaml` shows `universe: [RELIANCE.NS]`, restart the agent, and confirm `agent.log` prints `📌 Fixed universe: trading 1 user-selected stocks (scanner skipped)`.

- [ ] **Step 5: Final commit (if any stragglers)**

```bash
git add -A && git commit -m "chore: watchlist categories feature complete" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Run all `pytest` and `python -c` commands from the repo root** so the `agent`/`dashboard` packages import correctly.
- **Do not deploy to production as part of this plan.** Deployment is `./deploy-prod.sh backend` and is a separate, user-initiated step.
- **`sessions/_nse_sectors.json` is environment data**, never committed. The first `get_nse_sectors()` call fetches it; if niftyindices.com is unreachable in your environment, the unit tests still pass (they stub the network) and the feature degrades to "All sectors / individual tickers" per the spec's error handling.
- If exact line numbers have drifted, locate the anchor by the quoted code/symbol name rather than trusting the number.
