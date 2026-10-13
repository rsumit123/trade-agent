"""Token pricing for cost ledger. USD per 1M tokens.

Keep in sync with frontend/src/app/sessions/new/page.tsx MODEL_META.
Unknown models fall back to (in=1.0, out=4.0) — flagged in 'unknown'.
"""

# (input $/M, output $/M). OpenRouter prices checked against
# https://openrouter.ai/api/v1/models on 2026-10-12.
MODEL_PRICES = {
    # OpenRouter — paid
    "google/gemini-2.5-flash":             (0.30, 2.50),
    "google/gemini-2.5-flash-lite":        (0.10, 0.40),
    "openai/gpt-4o-mini":                  (0.15, 0.60),
    "meta-llama/llama-4-maverick":         (0.188, 0.652),
    "meta-llama/llama-4-scout":            (0.18, 0.59),
    "deepseek/deepseek-chat-v3-0324":      (0.29, 1.14),
    "deepseek/deepseek-r1":                (0.70, 2.50),
    # OpenRouter — free
    "qwen/qwen3-next-80b-a3b-instruct:free": (0.00, 0.00),
    "z-ai/glm-4.5-air:free":                 (0.00, 0.00),
    "meta-llama/llama-3.3-70b-instruct:free": (0.00, 0.00),
    "openai/gpt-oss-120b:free":              (0.00, 0.00),   # delisted by OpenRouter (now 404s)
    "openai/gpt-oss-120b":                   (0.037, 0.17),
    # Anthropic direct
    "claude-haiku-5-5":                    (0.10, 0.50),
    "claude-sonnet-5-5":                   (2.00, 10.00),
    "claude-opus-5-5":                     (4.00, 20.00),
    "claude-sonnet-4-5-20250929":          (3.00, 15.00),
    "claude-opus-4-0-20250514":            (15.00, 75.00),
    # OpenAI direct
    "gpt-4o":                              (2.50, 10.00),
    "gpt-4o-mini":                         (0.15, 0.60),
    "o3-mini":                             (1.10, 4.40),
}


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost estimate for a single LLM call."""
    in_price, out_price = MODEL_PRICES.get(model, (1.00, 4.00))
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def is_known(model: str) -> bool:
    return model in MODEL_PRICES
