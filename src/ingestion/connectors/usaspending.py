"""USAspending.gov connector for federal contract data.

Completely free, no API key required. Provides federal contract awards
by recipient company, useful as a revenue proxy for government contractors
and services companies.

API docs: https://api.usaspending.gov/docs/endpoints
Rate limit: No explicit limit, but be respectful (~5 req/sec).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

logger = structlog.get_logger("ingestion.usaspending")

BASE_URL = "https://api.usaspending.gov/api/v2"


@dataclass
class GovernmentContract:
    """A federal contract award."""

    recipient_name: str
    recipient_uei: str | None = None
    award_amount: float = 0.0
    total_obligation: float = 0.0
    awarding_agency: str | None = None
    funding_agency: str | None = None
    naics_code: str | None = None
    naics_description: str | None = None
    product_or_service: str | None = None
    award_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    place_of_performance_state: str | None = None
    award_id: str | None = None


@dataclass
class RecipientProfile:
    """Aggregated federal spending profile for a company."""

    name: str
    uei: str | None = None
    total_federal_spending: float = 0.0
    contract_count: int = 0
    top_agencies: list[str] = field(default_factory=list)
    top_naics: list[str] = field(default_factory=list)
    state: str | None = None
    recent_contracts: list[GovernmentContract] = field(default_factory=list)


class USASpendingConnector:
    """Connector for USAspending.gov federal contracts API."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Content-Type": "application/json"},
        )

    async def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search_recipients(
        self,
        keyword: str,
        limit: int = 10,
    ) -> list[dict]:
        """Search for federal award recipients by name."""
        payload = {
            "keyword": keyword,
            "limit": limit,
        }
        try:
            data = await self._post("autocomplete/recipient", payload)
            return data.get("results", [])
        except httpx.HTTPStatusError as e:
            logger.warning("recipient_search_error", keyword=keyword, error=str(e))
            return []

    async def get_spending_by_recipient(
        self,
        keyword: str,
        fiscal_year: int | None = None,
        limit: int = 50,
    ) -> list[GovernmentContract]:
        """Search federal contract awards by recipient name."""
        if fiscal_year is None:
            fiscal_year = datetime.now(timezone.utc).year

        filters: dict = {
            "keyword": keyword,
            "time_period": [
                {
                    "start_date": f"{fiscal_year - 2}-10-01",
                    "end_date": f"{fiscal_year}-09-30",
                }
            ],
            "award_type_codes": ["A", "B", "C", "D"],  # Contracts only
        }

        payload = {
            "filters": filters,
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Total Outlayed Amount",
                "Awarding Agency",
                "Funding Agency",
                "NAICS Code",
                "NAICS Description",
                "Product or Service Code",
                "Award Type",
                "Start Date",
                "End Date",
                "Place of Performance State Code",
                "recipient_uei",
            ],
            "limit": limit,
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
        }

        try:
            data = await self._post("search/spending_by_award", payload)
            results = data.get("results", [])
        except httpx.HTTPStatusError as e:
            logger.warning("spending_search_error", keyword=keyword, error=str(e))
            return []

        contracts = []
        for r in results:
            contracts.append(GovernmentContract(
                recipient_name=r.get("Recipient Name", ""),
                recipient_uei=r.get("recipient_uei"),
                award_amount=float(r.get("Award Amount") or 0),
                total_obligation=float(r.get("Total Outlayed Amount") or 0),
                awarding_agency=r.get("Awarding Agency"),
                funding_agency=r.get("Funding Agency"),
                naics_code=r.get("NAICS Code"),
                naics_description=r.get("NAICS Description"),
                product_or_service=r.get("Product or Service Code"),
                award_type=r.get("Award Type"),
                start_date=r.get("Start Date"),
                end_date=r.get("End Date"),
                place_of_performance_state=r.get("Place of Performance State Code"),
                award_id=r.get("Award ID"),
            ))

        logger.info(
            "spending_search_complete",
            keyword=keyword,
            contracts=len(contracts),
        )
        return contracts

    async def get_recipient_profile(
        self,
        company_name: str,
        fiscal_year: int | None = None,
    ) -> RecipientProfile | None:
        """Build an aggregated spending profile for a company.

        Returns None if no federal contracts found.
        """
        contracts = await self.get_spending_by_recipient(
            company_name, fiscal_year=fiscal_year
        )
        if not contracts:
            return None

        total = sum(c.award_amount for c in contracts)
        agencies = {}
        naics_codes = {}
        for c in contracts:
            if c.awarding_agency:
                agencies[c.awarding_agency] = agencies.get(c.awarding_agency, 0) + c.award_amount
            if c.naics_code:
                naics_codes[c.naics_code] = naics_codes.get(c.naics_code, 0) + c.award_amount

        top_agencies = sorted(agencies, key=agencies.get, reverse=True)[:5]
        top_naics = sorted(naics_codes, key=naics_codes.get, reverse=True)[:5]
        state = contracts[0].place_of_performance_state if contracts else None
        uei = next((c.recipient_uei for c in contracts if c.recipient_uei), None)

        return RecipientProfile(
            name=company_name,
            uei=uei,
            total_federal_spending=total,
            contract_count=len(contracts),
            top_agencies=top_agencies,
            top_naics=top_naics,
            state=state,
            recent_contracts=contracts[:10],
        )

    async def close(self) -> None:
        await self._client.aclose()
