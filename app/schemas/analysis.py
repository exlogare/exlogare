from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalysisOutput(BaseModel):
    """Strict output contract for AI RCA."""

    root_cause: str = Field(..., min_length=1, max_length=2000)
    explanation: str = Field(..., min_length=1, max_length=8000)
    fix_suggestion: str = Field(..., min_length=1, max_length=4000)
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_more_context: bool = False
    missing_context_hint: str | None = None
