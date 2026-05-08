"""Memory schemas — episodic, semantic, working memory."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class MemoryType(str, Enum):
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    IMMUTABLE = "IMMUTABLE"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    type: MemoryType
    content_hash: str
    text: str
    metadata: dict = Field(default_factory=dict)
    created_at: str = ""
