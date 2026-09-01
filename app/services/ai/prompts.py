from __future__ import annotations

from app.core.config import get_settings

NOT_A_LOG_HINT = "input_is_not_a_ci_log"


def get_system_prompt() -> str:
    return get_settings().resolve_system_prompt()


def build_user_prompt(project_path: str | None, log_excerpt: str) -> str:
    settings = get_settings()
    tpl = settings.resolve_user_prompt_template()
    header = f"Project: {project_path}\n" if project_path else ""
    return tpl.format(header=header, project_path=project_path or "", log_excerpt=log_excerpt)


_STRONG_LOG_MARKERS = (
    "error:",
    "error ",
    "fatal:",
    "fatal ",
    "traceback",
    "exception",
    "exit code",
    "exited with",
    "failed with",
    "npm err!",
    "npm error",
    "command failed",
    "segmentation fault",
    "panic:",
    "modulenotfounderror",
    "assertionerror",
    "permission denied",
    "timed out",
    "stack trace",
)

_WEAK_LOG_MARKERS = (
    "gitlab-ci",
    "gitlab_ci",
    "gitlab-runner",
    "jenkins",
    "pipeline",
    "pytest",
    "mvn ",
    "gradle",
    "docker",
    "kubectl",
    "npm ",
    "yarn ",
    "pip ",
    "apt-get",
    "runner:",
    "stage:",
    " job:",
    "$ ",
    "running on",
)


def looks_like_ci_log(text: str | None) -> bool:
    if not text:
        return False
    t = text.lower()
    if len(t.strip()) < 40:
        return False
    if any(m in t for m in _STRONG_LOG_MARKERS):
        return True
    weak_hits = sum(1 for m in _WEAK_LOG_MARKERS if m in t)
    return weak_hits >= 2
