"""Financial Modeling Prep (FMP) connector.

Free tier: 250 requests/day. Provides company profiles, financial statements,
key metrics, ratios, and enterprise values for public companies.

Note: M&A transactions and the stock screener require a paid plan.
Free tier covers: profile, income statement, balance sheet, ratios,
key metrics, enterprise values.

Get a free API key at: https://site.financialmodelingprep.com/developer
Set it via .env: FMP_API_KEY=your_key
"""

from datetime import datetime, timezone

import httpx
import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.fmp")

BASE_URL = "https://financialmodelingprep.com/stable"


class FMPConnector(BaseConnector):
    """Connector for Financial Modeling Prep API (stable endpoints)."""

    source_name = "fmp"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get(self, endpoint: str, **params: str | int) -> list | dict:
        params["apikey"] = self._api_key
        url = f"{BASE_URL}/{endpoint}"
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch_companies(
        self, since: datetime | None = None,
    ) -> list[CompanyRaw]:
        return []

    async def fetch_transactions(
        self, since: datetime | None = None,
    ) -> list[TransactionRecord]:
        return []

    async def fetch_profile(self, symbol: str) -> dict:
        """Fetch company profile for a single ticker."""
        data = await self._get("profile", symbol=symbol)
        if isinstance(data, list) and data:
            return data[0]
        return {}

    async def fetch_income_statement(self, symbol: str, limit: int = 3) -> list[dict]:
        """Fetch income statement (annual periods)."""
        data = await self._get("income-statement", symbol=symbol, limit=limit)
        return data if isinstance(data, list) else []

    async def fetch_key_metrics(self, symbol: str, limit: int = 1) -> list[dict]:
        """Fetch key financial metrics."""
        data = await self._get("key-metrics", symbol=symbol, limit=limit)
        return data if isinstance(data, list) else []

    async def fetch_ratios(self, symbol: str, limit: int = 1) -> list[dict]:
        """Fetch financial ratios."""
        data = await self._get("ratios", symbol=symbol, limit=limit)
        return data if isinstance(data, list) else []

    async def fetch_enterprise_value(self, symbol: str, limit: int = 1) -> list[dict]:
        """Fetch enterprise value data."""
        data = await self._get("enterprise-values", symbol=symbol, limit=limit)
        return data if isinstance(data, list) else []

    async def fetch_financials(self, symbol: str) -> dict:
        """Fetch a combined financial snapshot for a single ticker.

        Aggregates profile, income statement, key metrics, ratios, and
        enterprise value into a single dict.
        """
        result: dict = {"symbol": symbol, "source": self.source_name}

        try:
            profile = await self.fetch_profile(symbol)
            result["company_name"] = profile.get("companyName")
            result["sector"] = profile.get("sector")
            result["industry"] = profile.get("industry")
            result["country"] = profile.get("country")
            result["employee_count"] = profile.get("fullTimeEmployees")
            result["market_cap"] = profile.get("marketCap")
            result["website"] = profile.get("website")
            result["description"] = profile.get("description")
        except httpx.HTTPStatusError:
            pass

        try:
            income = await self.fetch_income_statement(symbol, limit=1)
            if income:
                stmt = income[0]
                result["revenue"] = stmt.get("revenue")
                result["gross_profit"] = stmt.get("grossProfit")
                result["operating_income"] = stmt.get("operatingIncome")
                result["ebitda"] = stmt.get("ebitda")
                result["net_income"] = stmt.get("netIncome")
                result["fiscal_year"] = stmt.get("fiscalYear")
                result["period"] = stmt.get("date")
                rev = stmt.get("revenue")
                ebitda = stmt.get("ebitda")
                if rev and ebitda and rev > 0:
                    result["ebitda_margin"] = ebitda / rev
        except httpx.HTTPStatusError:
            pass

        try:
            ev_data = await self.fetch_enterprise_value(symbol, limit=1)
            if ev_data:
                ev = ev_data[0]
                result["enterprise_value"] = ev.get("enterpriseValue")
                result["shares_outstanding"] = ev.get("numberOfShares")
        except httpx.HTTPStatusError:
            pass

        try:
            ratios = await self.fetch_ratios(symbol, limit=1)
            if ratios:
                r = ratios[0]
                result["gross_margin"] = r.get("grossProfitMargin")
                result["operating_margin"] = r.get("operatingProfitMargin")
                result["net_margin"] = r.get("netProfitMargin")
                result["roe"] = r.get("returnOnEquity")
                result["roa"] = r.get("returnOnAssets")
                result["current_ratio"] = r.get("currentRatio")
                result["debt_to_equity"] = r.get("debtEquityRatio")
        except httpx.HTTPStatusError:
            pass

        try:
            metrics = await self.fetch_key_metrics(symbol, limit=1)
            if metrics:
                m = metrics[0]
                result["ev_ebitda"] = m.get("enterpriseValueOverEBITDA")
                result["ev_revenue"] = m.get("evToOperatingCashFlow")
                result["revenue_per_share"] = m.get("revenuePerShare")
                result["pe_ratio"] = m.get("peRatio")
                result["pb_ratio"] = m.get("pbRatio")
        except httpx.HTTPStatusError:
            pass

        return result

    async def close(self) -> None:
        await self._client.aclose()
