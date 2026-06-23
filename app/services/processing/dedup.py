from __future__ import annotations


def dedup_consecutive(lines: list[str]) -> list[str]:
    """Collapse consecutive duplicate lines (common in CI retries / spinner noise)."""
    out: list[str] = []
    last: str | None = None
    count = 0
    for line in lines:
        if line == last:
            count += 1
            continue
        if count > 1 and out:
            out[-1] = f"{out[-1]} [xN duplicates]"
        out.append(line)
        last = line
        count = 1
    if count > 1 and out:
        out[-1] = f"{out[-1]} [xN duplicates]"
    return out


def dedup_blocks(
    lines: list[str],
    window: int = 50,
    min_block: int = 5,
) -> list[str]:
    """Collapse repeated *adjacent multi-line blocks*."""
    if not lines:
        return lines
    n = len(lines)
    keep = [True] * n
    markers: dict[int, int] = {}
    i = 0
    while i < n:
        matched = False
        max_size = min(window, (n - i) // 2)
        for size in range(max_size, min_block - 1, -1):
            base = lines[i : i + size]
            if not base:
                continue
            repeats = 0
            j = i + size
            while j + size <= n and lines[j : j + size] == base:
                repeats += 1
                for k in range(j, j + size):
                    keep[k] = False
                j += size
            if repeats > 0:
                markers[i + size - 1] = repeats + 1
                i = j
                matched = True
                break
        if not matched:
            i += 1
    out: list[str] = []
    for idx, line in enumerate(lines):
        if not keep[idx]:
            continue
        out.append(line)
        if idx in markers:
            out.append(f"[previous block repeated x{markers[idx]} times]")
    return out
