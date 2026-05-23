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
    "Financial Services": "ind_niftyfinancelist",
    "Oil & Gas":          "ind_niftyoilgaslist",
    "Healthcare":         "ind_niftyhealthcarelist",
    "Consumer Durables":  "ind_niftyconsumerdurableslist",
    "PSU Bank":           "ind_niftypsubanklist",
    "Private Bank":       "ind_nifty_privatebanklist",
}

_CSV_BASE = "https://niftyindices.com/IndexConstituent/{slug}.csv"
_UA = "Mozilla/5.0 (compatible; AlphaAgent/1.0)"


def parse_constituents_csv(text: str) -> List[str]:
    """Parse a NIFTY constituent CSV into ['TATASTEEL.NS', ...]."""
    out: List[str] = []
    reader = _csv.DictReader(io.StringIO(text))
    for row in reader:
        sym = (row.get("Symbol") or "").strip().upper()
        # Skip NSE placeholder symbols (e.g. DUMMYVEDL1) used for corporate
        # actions — they are not tradeable instruments.
        if sym and not sym.startswith("DUMMY"):
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
