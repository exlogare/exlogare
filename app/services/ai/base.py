from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.analysis import AnalysisOutput
from app.services.ai.prompts import (
    NOT_A_LOG_HINT,
    build_user_prompt,
    get_system_prompt,
    looks_like_ci_log,
)

log = get_logger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_FIRST_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(content: str) -> dict:
    content = (content or "").strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    fenced = _JSON_FENCE_RE.search(content)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    match = _FIRST_OBJ_RE.search(content)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _parse_analysis(content: str) -> AnalysisOutput:
    data = _extract_json(content)
    if not data:
        log.warning("llm.invalid_json", preview=content[:200])
        return AnalysisOutput(
            root_cause="Analyzer output could not be parsed",
            explanation="LLM did not return valid JSON.",
            fix_suggestion="Retry analysis or adjust LLM_JSON_MODE / prompt.",
            severity="low",
            confidence=0.2,
            needs_more_context=True,
            missing_context_hint="llm_parse_error",
        )
    try:
        return AnalysisOutput(**data)
    except ValidationError as exc:
        log.warning("llm.invalid_schema", error=str(exc))
        return AnalysisOutput(
            root_cause="Analyzer output failed schema validation",
            explanation=f"Schema errors: {exc}",
            fix_suggestion="Retry analysis; tighten the system prompt schema.",
            severity="low",
            confidence=0.2,
            needs_more_context=True,
            missing_context_hint="llm_schema_error",
        )


class LLMAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, project_path: str | None, log_excerpt: str) -> AnalysisOutput: ...


def _not_a_log_output() -> AnalysisOutput:
    return AnalysisOutput(
        root_cause="Not a CI/CD log",
        explanation="Input does not look like a CI/CD pipeline or job log.",
        fix_suggestion="Send the actual job log output from the failing step.",
        severity="low",
        confidence=0.0,
        needs_more_context=True,
        missing_context_hint=NOT_A_LOG_HINT,
    )


class StubAnalyzer(LLMAnalyzer):
    async def analyze(self, project_path: str | None, log_excerpt: str) -> AnalysisOutput:
        return AnalysisOutput(
            root_cause="LLM not configured",
            explanation="Set LLM_ENABLED=true and configure LLM_* variables in .env.",
            fix_suggestion="Configure an OpenAI-compatible endpoint and restart the API.",
            severity="low",
            confidence=0.0,
            needs_more_context=True,
            missing_context_hint="llm_disabled",
        )


class OpenAICompatibleAnalyzer(LLMAnalyzer):
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        settings = get_settings()
        kwargs: dict = {"api_key": settings.llm_api_key or "local"}
        if settings.llm_base_url.strip():
            kwargs["base_url"] = settings.llm_base_url.strip()
        self._client = AsyncOpenAI(**kwargs)
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._max_tokens = settings.llm_max_tokens
        self._json_mode = settings.llm_json_mode

    async def analyze(self, project_path: str | None, log_excerpt: str) -> AnalysisOutput:
        if not looks_like_ci_log(log_excerpt):
            return _not_a_log_output()

        messages = [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": build_user_prompt(project_path, log_excerpt)},
        ]
        kwargs: dict = {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if self._json_mode:
            try:
                resp = await self._client.chat.completions.create(
                    response_format={"type": "json_object"},
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                log.info("llm.response_format_unsupported", error=str(exc))
                resp = await self._client.chat.completions.create(**kwargs)
        else:
            resp = await self._client.chat.completions.create(**kwargs)

        content = resp.choices[0].message.content or "{}"
        return _parse_analysis(content)


def get_analyzer() -> LLMAnalyzer:
    settings = get_settings()
    if settings.llm_enabled and settings.llm_api_key:
        return OpenAICompatibleAnalyzer()
    return StubAnalyzer()
