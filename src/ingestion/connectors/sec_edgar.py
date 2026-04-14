"""SEC EDGAR connector via edgartools.

Completely free, no API key required. Provides full financial statements
(revenue, EBITDA, margins) for all US public companies from XBRL filings.

Requires: pip install edgartools
Set identity via environment variable: EDGAR_IDENTITY="Your Name email@example.com"
"""

from datetime import datetime, timezone

import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.sec_edgar")

# Standard XBRL concepts for extracting financials
REVENUE_CONCEPTS = [
    "Revenue",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]

OPERATING_INCOME_CONCEPTS = [
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
]


def _extract_value(df, concepts: list[str], period_col: str) -> float | None:
    """Extract the first matching concept value from a statement dataframe."""
    for concept in concepts:
        mask = df["concept"].str.contains(concept, case=False, na=False)
        non_dim = mask & (~df["dimension"])
        rows = df[non_dim]
        if not rows.empty:
            val = rows.iloc[0].get(period_col)
            if val is not None and not (isinstance(val, float) and val != val):
                return float(val)
    return None


class SECEdgarConnector(BaseConnector):
    """Connector for SEC EDGAR via edgartools library."""

    source_name = "sec_edgar"

    def __init__(self, identity: str = "deal-sourcing-poc research@example.com") -> None:
        from edgar import set_identity
        set_identity(identity)
        self._identity = identity

    async def fetch_companies(
        self, since: datetime | None = None
    ) -> list[CompanyRaw]:
        return []

    async def fetch_transactions(
        self, since: datetime | None = None
    ) -> list[TransactionRecord]:
        return []

    def get_company_financials(self, ticker: str) -> dict:
        """Fetch full financial data for a single public company from 10-K filings.

        Returns dict with: company_name, cik, revenue, ebitda (estimated),
        operating_income, net_income, total_assets, ebitda_margin, fiscal_years, etc.
        """
        from edgar import Company

        logger.info("edgar.fetch_financials", ticker=ticker)
        result: dict = {"ticker": ticker, "source": self.source_name}

        try:
            company = Company(ticker)
            result["company_name"] = company.name
            result["cik"] = company.cik
        except Exception as e:
            logger.warning("edgar.company_not_found", ticker=ticker, error=str(e))
            result["error"] = str(e)
            return result

        try:
            filings = company.get_filings(form="10-K")
            if not filings or len(filings) == 0:
                result["error"] = "No 10-K filings found"
                return result

            latest = filings[0]
            result["filing_date"] = str(latest.filing_date)

            tenk = latest.obj()
            financials = tenk.financials

            income = financials.income_statement()
            df = income.to_dataframe()
            period_cols = [c for c in df.columns if "(FY)" in c]

            if not period_cols:
                result["error"] = "No annual periods found in income statement"
                return result

            fiscal_years = []
            for col in sorted(period_cols, reverse=True):
                year_data: dict = {"period": col}

                revenue = _extract_value(df, REVENUE_CONCEPTS, col)
                operating_income = _extract_value(df, OPERATING_INCOME_CONCEPTS, col)

                # Extract D&A from cash flow for EBITDA estimation
                da = None
                try:
                    cf = financials.cash_flow_statement()
                    cf_df = cf.to_dataframe()
                    if col in cf_df.columns:
                        da = _extract_value(
                            cf_df,
                            ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
                            col,
                        )
                except Exception:
                    pass

                ebitda = None
                if operating_income is not None and da is not None:
                    ebitda = operating_income + abs(da)
                elif operating_income is not None:
                    ebitda = operating_income * 1.15

                year_data["revenue"] = revenue
                year_data["operating_income"] = operating_income
                year_data["depreciation_amortization"] = da
                year_data["ebitda"] = ebitda
                if revenue and ebitda and revenue > 0:
                    year_data["ebitda_margin"] = ebitda / revenue
                if revenue and operating_income and revenue > 0:
                    year_data["operating_margin"] = operating_income / revenue

                fiscal_years.append(year_data)

            result["fiscal_years"] = fiscal_years

            if fiscal_years:
                latest_fy = fiscal_years[0]
                result["revenue"] = latest_fy.get("revenue")
                result["ebitda"] = latest_fy.get("ebitda")
                result["ebitda_margin"] = latest_fy.get("ebitda_margin")
                result["operating_income"] = latest_fy.get("operating_income")

                if len(fiscal_years) >= 2:
                    prev = fiscal_years[1]
                    if latest_fy.get("revenue") and prev.get("revenue") and prev["revenue"] > 0:
                        result["revenue_growth"] = (
                            latest_fy["revenue"] - prev["revenue"]
                        ) / prev["revenue"]

        except Exception as e:
            logger.warning("edgar.financials_error", ticker=ticker, error=str(e))
            result["error"] = str(e)

        return result

    def get_bulk_financials(self, tickers: list[str]) -> list[dict]:
        """Fetch financials for multiple tickers."""
        results = []
        for ticker in tickers:
            try:
                data = self.get_company_financials(ticker)
                results.append(data)
            except Exception as e:
                logger.warning("edgar.bulk_error", ticker=ticker, error=str(e))
                results.append({"ticker": ticker, "error": str(e)})
        return results
