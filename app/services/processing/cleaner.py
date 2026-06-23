from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_HASH_RE = re.compile(r"\b[0-9a-fA-F]{32,64}\b")
_SECRET_RE = re.compile(
    r"(?i)(?:password|secret|token|api[_-]?key|bearer)\s*[:=]\s*\S+"
)


def strip_noise(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = _TIMESTAMP_RE.sub("<ts>", text)
    text = _IP_RE.sub("<ip>", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _LONG_HASH_RE.sub("<hash>", text)
    text = _SECRET_RE.sub("<redacted>", text)
    return text
