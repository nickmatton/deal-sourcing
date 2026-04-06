from abc import ABC, abstractmethod
from datetime import datetime, timezone

import structlog

from src.common.schemas.ingestion import CompanyRaw, TransactionRecord

logger = structlog.get_logger("ingestion")


class BaseConnector(ABC):
    """Base class for all data source connectors."""

    source_name: str

    @abstractmethod
    async def fetch_companies(
        self, since: datetime | None = None
    ) -> list[CompanyRaw]:
        ...

    @abstractmethod
    async def fetch_transactions(
        self, since: datetime | None = None
    ) -> list[TransactionRecord]:
        ...

    async def health_check(self) -> bool:
        return True

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
