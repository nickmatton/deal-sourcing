from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    COMPANY = "company"
    PERSON = "person"
    TRANSACTION = "transaction"
    THESIS = "thesis"


class SourceRecord(BaseModel):
    source: str
    source_id: str
    raw_name: str
    domain: str | None = None
    geography: str | None = None
    ingested_at: str  # ISO 8601


class CanonicalEntity(BaseModel):
    entity_id: str = Field(default_factory=lambda: str(uuid4()))
    entity_type: EntityType
    canonical_name: str
    domain: str | None = None
    geography: str | None = None
    source_records: list[SourceRecord] = Field(default_factory=list)
    confidence: float = 1.0
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601

    def add_source(self, record: SourceRecord) -> None:
        self.source_records.append(record)
