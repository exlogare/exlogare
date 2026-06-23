from __future__ import annotations

from pydantic import BaseModel


class GitLabWebhookPayload(BaseModel):
    object_kind: str
    class Config:
        extra = "allow"
