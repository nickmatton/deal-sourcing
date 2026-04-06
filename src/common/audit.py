import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("audit")


class AuditAction(StrEnum):
    MODEL_PREDICTION = "model_prediction"
    HUMAN_OVERRIDE = "human_override"
    OUTREACH_SENT = "outreach_sent"
    DEAL_STAGE_CHANGE = "deal_stage_change"
    DATA_ACCESS = "data_access"
    ENTITY_MERGE = "entity_merge"
    ENTITY_SPLIT = "entity_split"
    SCORE_GENERATED = "score_generated"
    MEMO_GENERATED = "memo_generated"


class AuditEntry(BaseModel):
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    action: AuditAction
    actor: str  # system component or user id
    entity_id: str | None = None
    model_version: str | None = None
    details: dict = Field(default_factory=dict)
    stage: str | None = None


class AuditLogger:
    """Append-only audit trail logger.

    In production this writes to an immutable store (e.g. S3 append, DB).
    For local dev it writes to a JSONL file.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path or Path("audit_trail.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: AuditEntry) -> None:
        record = entry.model_dump()
        with open(self._log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        logger.info("audit_logged", action=entry.action, entity_id=entry.entity_id)

    def query(
        self,
        entity_id: str | None = None,
        action: AuditAction | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        results: list[AuditEntry] = []
        if not self._log_path.exists():
            return results
        with open(self._log_path) as f:
            for line in f:
                entry = AuditEntry.model_validate_json(line.strip())
                if entity_id and entry.entity_id != entity_id:
                    continue
                if action and entry.action != action:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
        return results
