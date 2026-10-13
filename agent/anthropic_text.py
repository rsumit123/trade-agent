"""
Read the text out of an Anthropic Messages response.

Claude 5.x models return a `thinking` block before the answer, so
`response.content[0].text` raises AttributeError ('ThinkingBlock' object has
no attribute 'text'). Thinking also counts against max_tokens: a 300-token
budget can be spent entirely on thinking, leaving no text block at all.

Use `response_text()` instead of indexing, and budget with `thinking_budget()`.
"""

# Headroom for the thinking block on top of what the answer itself needs.
THINKING_HEADROOM = 2000


def response_text(response) -> str:
    """Concatenate the text blocks; raise if the model returned none."""
    text = "".join(
        getattr(b, "text", "") or "" for b in response.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise ValueError(
            f"no text in response (stop_reason={getattr(response, 'stop_reason', None)}) — "
            "thinking may have used the whole max_tokens budget"
        )
    return text


def thinking_budget(answer_tokens: int) -> int:
    """max_tokens for an Anthropic call whose answer needs ~answer_tokens."""
    return answer_tokens + THINKING_HEADROOM
