"""
Versioned prompt templates.

Prompts live in agent/prompts/<name>/<version>.md and are indexed by
agent/prompts/registry.json, which records the active version plus the
sha256, source commit and a one-line summary for every version.

    rollback  → change "active" in registry.json (one line)
    A/B test  → set `prompt_version` on a session's config.yaml
    new rev   → add vN+1.md + a registry entry; never edit a released file

Released versions are immutable: tests/test_prompt_registry.py fails if a
file's hash no longer matches the registry. At runtime a mismatch is only
logged, so a bad edit can never stop an agent from trading.

The registry is re-read every call (it's tiny), so changing the active
version takes effect on each agent's next cycle without a restart.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
REGISTRY_PATH = PROMPTS_DIR / "registry.json"


@dataclass(frozen=True)
class PromptVersion:
    name: str          # "decision"
    version: str       # "v4"
    template: str
    sha256: str

    @property
    def id(self) -> str:
        return f"{self.name}_{self.version}"   # "decision_v4" — used as the Langfuse trace version


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text())


def get_prompt(name: str, version: Optional[str] = None) -> PromptVersion:
    """Load a prompt template. `version=None` → the registry's active version.

    An unknown requested version falls back to the active one (logged), so a
    typo in a session config degrades gracefully instead of crashing the agent.
    """
    entry = load_registry()[name]
    versions = entry["versions"]
    if version and version not in versions:
        logger.warning(f"Unknown {name} prompt version '{version}', using active '{entry['active']}'")
        version = None
    version = version or entry["active"]

    raw = (PROMPTS_DIR / name / f"{version}.md").read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != versions[version]["sha256"]:
        logger.warning(f"Prompt {name}_{version} was edited after release (hash mismatch) — "
                       f"add a new version instead of editing a released one")
    return PromptVersion(name=name, version=version, template=raw.decode("utf-8"), sha256=sha)


def short_hash(text: str) -> str:
    """8-char content hash, used to fingerprint per-session prompt overrides."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8] if text else ""
