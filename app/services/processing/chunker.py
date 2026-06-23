from __future__ import annotations


def tail_lines(text: str, n: int) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    if n <= 0:
        return lines
    return lines[-n:]


def approx_token_count(text: str) -> int:
    # Cheap approximation (chars / 4) to avoid tokenizer dependency in MVP.
    return max(1, len(text) // 4)


def truncate_to_budget(lines: list[str], max_tokens: int) -> str:
    """Keep the *last* lines that fit within the token budget."""
    if not lines:
        return ""
    budget = max_tokens
    kept: list[str] = []
    for line in reversed(lines):
        cost = approx_token_count(line) + 1
        if cost > budget:
            break
        kept.append(line)
        budget -= cost
    return "\n".join(reversed(kept))
