"""
Optional LLM tracing via Langfuse.

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set and the
`langfuse` package is importable. Otherwise every helper is a no-op.

Tracing must never break trading: all Langfuse calls are wrapped so that a
tracing failure is logged once and swallowed, while exceptions raised by the
traced code itself still propagate normally.

Structure of one decision cycle:
    decision_cycle (agent)           ← session_id, tags: market / model / provider
      ├─ llm_call_0 (generation)     ← prompt, response, token usage
      ├─ place_trade (tool)          ← tool input / result
      ├─ llm_call_1 (generation)
      ...
"""

import logging
import os
from contextlib import contextmanager, ExitStack
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_client = None
_init_done = False
_warned = False


def _get_client():
    """Lazily create the Langfuse client; return None when tracing is off."""
    global _client, _init_done
    if _init_done:
        return _client
    _init_done = True
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import get_client
        _client = get_client()  # reads LANGFUSE_* env vars (host, sample rate, ...)
        logger.info("🔭 Langfuse tracing enabled")
    except Exception as e:
        logger.warning(f"Langfuse tracing disabled (init failed): {e}")
        _client = None
    return _client


def is_enabled() -> bool:
    return _get_client() is not None


def _warn_once(e: Exception):
    global _warned
    if not _warned:
        _warned = True
        logger.warning(f"Langfuse tracing error (further errors suppressed): {e}")


class _Handle:
    """Wraps a Langfuse observation; update() never raises."""

    def __init__(self, obs=None):
        self._obs = obs

    def update(self, **kwargs):
        if self._obs is None:
            return
        try:
            self._obs.update(**kwargs)
        except Exception as e:
            _warn_once(e)


@contextmanager
def _observe(name: str, as_type: str, propagate: Optional[Dict] = None, **kwargs):
    lf = _get_client()
    if lf is None:
        yield _Handle()
        return

    stack = ExitStack()
    handle = _Handle()
    try:
        if propagate:
            from langfuse import propagate_attributes
            stack.enter_context(propagate_attributes(**propagate))
        obs = stack.enter_context(
            lf.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        )
        handle = _Handle(obs)
    except Exception as e:
        _warn_once(e)

    with stack:
        try:
            yield handle
        except Exception as e:
            handle.update(level="ERROR", status_message=str(e)[:500])
            raise


def decision_cycle(config, input: Any = None, metadata: Optional[Dict] = None,
                   prompt: Optional[Dict] = None):
    """Root observation for one decision loop, grouped by session_id in Langfuse.

    `prompt` (from _format_system_prompt) sets the trace version to the prompt
    id, e.g. "decision_v4", so traces can be filtered and compared by version.
    """
    sc = getattr(config, "_session_config", None)
    session_id = getattr(sc, "session_id", "") or "default"
    market = getattr(sc, "market", "") or ""
    provider = getattr(config, "llm_provider", "")
    model = getattr(sc, "llm_model", "") or ""
    prompt_id = (prompt or {}).get("id", "")
    tags: List[str] = [t for t in (market, provider, model) if t]
    if prompt_id:
        tags.append(f"prompt:{prompt_id}")
    return _observe(
        "decision_cycle",
        "agent",
        propagate={
            "session_id": session_id,
            "user_id": getattr(sc, "user_email", "") or None,
            "tags": tags,
            "trace_name": f"decision_cycle:{session_id}",
            "version": prompt_id or None,
            "metadata": {"market": market, "provider": provider},
        },
        input=input,
        metadata=metadata,
    )


def generation(name: str, model: str, input: Any = None,
               model_parameters: Optional[Dict] = None):
    return _observe(name, "generation", model=model, input=input,
                    model_parameters=model_parameters)


def tool(name: str, input: Any = None):
    return _observe(name, "tool", input=input)


def flush():
    lf = _get_client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception as e:
        _warn_once(e)
