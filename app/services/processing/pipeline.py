from __future__ import annotations

from app.services.processing.chunker import tail_lines, truncate_to_budget
from app.services.processing.cleaner import strip_noise
from app.services.processing.dedup import dedup_blocks, dedup_consecutive

DEFAULT_TAIL_LINES = 500
DEFAULT_TOKEN_BUDGET = 2500


def process_log_for_llm(
    raw_log: str,
    tail: int | None = None,
    token_budget: int | None = None,
) -> str:
    from app.core.config import get_settings

    settings = get_settings()
    tail = tail if tail is not None else settings.llm_tail_lines
    token_budget = token_budget if token_budget is not None else settings.llm_token_budget
    """Clean -> tail -> dedup (consecutive + repeated blocks) -> budget-trim."""
    cleaned = strip_noise(raw_log or "")
    lines = tail_lines(cleaned, tail)
    lines = dedup_consecutive(lines)
    lines = dedup_blocks(lines)
    return truncate_to_budget(lines, token_budget)
