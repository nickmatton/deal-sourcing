"""Yahoo Finance connector via yfinance.

Completely free, no API key required. Provides company profiles,
financials, market data for public companies. Good for quick lookups
and enrichment.

Requires: pip install yfinance
"""

from datetime import datetime, timezone

import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.yfinance")


class YFinanceConnector(BaseConnector):
    """Connector for Yahoo Finance via yfinance library."""

    source_name = "yfinance"

    async def fetch_companies(
        self, since: datetime | None = None
    ) -> list[CompanyRaw]:
        return []

    async def fetch_transactions(
        self, since: datetime | None = None
    ) -> list[TransactionRecord]:
        return []

    def get_company_profile(self, ticker: str) -> dict:
        """Fetch company profile and key financials for a single ticker.

        Returns dict with: company_name, sector, industry, employees,
        revenue, ebitda, ebitda_margin, market_cap, ev, ev_ebitda, etc.
        """
        import yfinance as yf

        logger.info("yfinance.fetch_profile", ticker=ticker)
        result: dict = {"ticker": ticker, "source": self.source_name}

        try:
            t = yf.Ticker(ticker)
            info = t.info

            result["company_name"] = info.get("longName") or info.get("shortName")
            result["sector"] = info.get("sector")
            result["industry"] = info.get("industry")
            result["city"] = info.get("city")
            result["state"] = info.get("state")
            result["country"] = info.get("country")
            result["website"] = info.get("website")
            result["description"] = info.get("longBusinessSummary")
            result["employee_count"] = info.get("fullTimeEmployees")

            result["revenue"] = info.get("totalRevenue")
            result["ebitda"] = info.get("ebitda")
            result["ebitda_margin"] = info.get("ebitdaMargins")
            result["gross_margin"] = info.get("grossMargins")
            result["operating_margin"] = info.get("operatingMargins")
            result["revenue_growth"] = info.get("revenueGrowth")

            result["market_cap"] = info.get("marketCap")
            result["enterprise_value"] = info.get("enterpriseValue")
            result["ev_ebitda"] = info.get("enterpriseToEbitda")
            result["ev_revenue"] = info.get("enterpriseToRevenue")
            result["trailing_pe"] = info.get("trailingPE")
            result["forward_pe"] = info.get("forwardPE")

            result["beta"] = info.get("beta")
            result["dividend_yield"] = info.get("dividendYield")
            result["payout_ratio"] = info.get("payoutRatio")

        except Exception as e:
            logger.warning("yfinance.error", ticker=ticker, error=str(e))
            result["error"] = str(e)

        return result

    def get_bulk_profiles(self, tickers: list[str]) -> list[dict]:
        """Fetch profiles for multiple tickers."""
        results = []
        for ticker in tickers:
            try:
                data = self.get_company_profile(ticker)
                results.append(data)
            except Exception as e:
                logger.warning("yfinance.bulk_error", ticker=ticker, error=str(e))
                results.append({"ticker": ticker, "error": str(e)})
        return results
