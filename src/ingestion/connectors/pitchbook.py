from datetime import datetime

import httpx
import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.pitchbook")

OWNERSHIP_MAP = {
    "founder-owned": OwnershipType.FOUNDER,
    "family-owned": OwnershipType.FAMILY,
    "pe-backed": OwnershipType.PE_BACKED,
    "public": OwnershipType.PUBLIC,
    "vc-backed": OwnershipType.VC_BACKED,
}


class PitchBookConnector(BaseConnector):
    """Connector for PitchBook API.

    Requires a PitchBook Data API subscription and API key.
    """

    source_name = "pitchbook"

    def __init__(self, api_key: str, base_url: str = "https://api.pitchbook.com/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    async def fetch_companies(
        self, since: datetime | None = None
    ) -> list[CompanyRaw]:
        logger.info("pitchbook.fetch_companies", since=since)
        params: dict = {"limit": 1000}
        if since:
            params["updated_since"] = since.isoformat()

        response = await self._client.get("/companies", params=params)
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("companies", []):
            raw = CompanyRaw(
                source=self.source_name,
                source_id=item["id"],
                name=item["name"],
                domain=item.get("website"),
                description=item.get("description"),
                industry=item.get("primary_industry"),
                naics_code=item.get("naics"),
                hq_city=item.get("hq_city"),
                hq_state=item.get("hq_state"),
                hq_country=item.get("hq_country", "US"),
                founded_year=item.get("founded_year"),
                employee_count=item.get("employees"),
                estimated_revenue=item.get("revenue"),
                estimated_ebitda=item.get("ebitda"),
                ownership_type=OWNERSHIP_MAP.get(
                    item.get("ownership_status", ""), OwnershipType.UNKNOWN
                ),
                funding_total=item.get("total_raised"),
                last_funding_date=item.get("last_funding_date"),
                last_funding_round=item.get("last_funding_type"),
                executives=item.get("key_people", []),
                ingested_at=self._now_iso(),
            )
            results.append(raw)

        logger.info("pitchbook.fetch_companies.done", count=len(results))
        return results

    async def fetch_transactions(
        self, since: datetime | None = None
    ) -> list[TransactionRecord]:
        logger.info("pitchbook.fetch_transactions", since=since)
        params: dict = {"limit": 1000}
        if since:
            params["completed_since"] = since.isoformat()

        response = await self._client.get("/deals", params=params)
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("deals", []):
            record = TransactionRecord(
                transaction_id=item["id"],
                target_name=item["target_name"],
                buyer_name=item.get("buyer_name"),
                buyer_type=item.get("buyer_type"),
                deal_type=item.get("deal_type"),
                sector=item.get("sector"),
                enterprise_value=item.get("enterprise_value"),
                ev_ebitda_multiple=item.get("ev_ebitda"),
                ev_revenue_multiple=item.get("ev_revenue"),
                target_revenue=item.get("target_revenue"),
                target_ebitda=item.get("target_ebitda"),
                target_ebitda_margin=item.get("target_ebitda_margin"),
                target_revenue_growth=item.get("target_revenue_growth"),
                deal_date=item["deal_date"],
                geography=item.get("geography"),
                source=self.source_name,
            )
            results.append(record)

        logger.info("pitchbook.fetch_transactions.done", count=len(results))
        return results

    async def close(self) -> None:
        await self._client.aclose()
