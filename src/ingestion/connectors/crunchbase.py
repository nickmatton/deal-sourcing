from datetime import datetime

import httpx
import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.crunchbase")


class CrunchbaseConnector(BaseConnector):
    """Connector for Crunchbase API."""

    source_name = "crunchbase"

    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.crunchbase.com/api/v4",
            headers={"X-cb-user-key": api_key},
            timeout=60.0,
        )

    async def fetch_companies(
        self, since: datetime | None = None
    ) -> list[CompanyRaw]:
        logger.info("crunchbase.fetch_companies", since=since)
        payload: dict = {
            "field_ids": [
                "identifier", "short_description", "categories",
                "location_identifiers", "founded_on", "num_employees_enum",
                "revenue_range", "website_url", "funding_total",
            ],
            "limit": 1000,
        }
        if since:
            payload["query"] = [
                {"type": "predicate", "field_id": "updated_at",
                 "operator_id": "gte", "values": [since.isoformat()]}
            ]

        response = await self._client.post(
            "/searches/organizations", json=payload
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("entities", []):
            props = item.get("properties", {})
            loc = props.get("location_identifiers", [{}])
            loc_parts = loc[0] if loc else {}

            results.append(CompanyRaw(
                source=self.source_name,
                source_id=item["uuid"],
                name=props.get("identifier", {}).get("value", ""),
                domain=props.get("website_url"),
                description=props.get("short_description"),
                industry=_first_category(props.get("categories", [])),
                hq_city=loc_parts.get("city"),
                hq_state=loc_parts.get("region"),
                hq_country=loc_parts.get("country", "US"),
                founded_year=_parse_year(props.get("founded_on")),
                employee_count=_parse_employee_enum(
                    props.get("num_employees_enum")
                ),
                estimated_revenue=_parse_revenue_range(
                    props.get("revenue_range")
                ),
                ownership_type=OwnershipType.UNKNOWN,
                funding_total=_parse_funding(props.get("funding_total")),
                ingested_at=self._now_iso(),
            ))

        logger.info("crunchbase.fetch_companies.done", count=len(results))
        return results

    async def fetch_transactions(
        self, since: datetime | None = None
    ) -> list[TransactionRecord]:
        # Crunchbase has limited transaction data; return empty for now
        return []

    async def close(self) -> None:
        await self._client.aclose()


def _first_category(categories: list) -> str | None:
    if categories and isinstance(categories[0], dict):
        return categories[0].get("value")
    if categories:
        return str(categories[0])
    return None


def _parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return int(date_str[:4])
    except (ValueError, IndexError):
        return None


EMPLOYEE_ENUM_MAP = {
    "c_00001_00010": 5,
    "c_00011_00050": 30,
    "c_00051_00100": 75,
    "c_00101_00250": 175,
    "c_00251_00500": 375,
    "c_00501_01000": 750,
    "c_01001_05000": 3000,
    "c_05001_10000": 7500,
    "c_10001_max": 15000,
}


def _parse_employee_enum(enum_val: str | None) -> int | None:
    if not enum_val:
        return None
    return EMPLOYEE_ENUM_MAP.get(enum_val)


def _parse_revenue_range(rev_range: str | None) -> float | None:
    if not rev_range:
        return None
    # Crunchbase returns ranges like "r_01000000" (1M) — return midpoint
    return None  # Implement based on actual API response format


def _parse_funding(funding: dict | None) -> float | None:
    if not funding:
        return None
    return funding.get("value_usd")
