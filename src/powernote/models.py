from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExtractedEntry(BaseModel):
    datetime_hint: str | None = None
    tags: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    entries: list[ExtractedEntry] = Field(default_factory=list)
    new_tags: list[str] = Field(default_factory=list)


class DiaryEntry(BaseModel):
    datetime: datetime
    tags: list[str]
    facts: list[str]
    source: str
    raw_text: str


class EmbeddingRecord(BaseModel):
    entry_key: str
    model: str
    embedding: list[float]
