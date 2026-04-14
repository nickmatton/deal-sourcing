"""Free-data enrichment — grounds LLM-estimated financials with real data.

After ClaudeResearch discovers companies, this module attempts to find
matching public-company data via yfinance, SEC EDGAR, and FMP, then
merges verified financials back into the CompanyRaw records.

Priority order (most authoritative first):
  1. SEC EDGAR (10-K XBRL filings — audited)
  2. FMP (aggregated financials — requires free API key)
  3. yfinance (Yahoo Finance profiles — convenient but less reliable)

Enrichment is best-effort: if a ticker can't be resolved or a source
is unavailable, the original LLM estimates are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import structlog

from src.common.schemas.ingestion import CompanyRaw

logger = structlog.get_logger("ingestion.enrichment")


@dataclass
class EnrichmentResult:
    """Tracks what was enriched and from where."""

    company_name: str
    ticker: str | None = None
    sources_used: list[str] = field(default_factory=list)
    fields_updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_ticker(company_name: str) -> str | None:
    """Attempt to resolve a stock ticker from a company name via yfinance search."""
    try:
        import yfinance as yf

        results = yf.Search(company_name)
        quotes = results.quotes
        if not quotes:
            return None
        best = quotes[0]
        symbol = best.get("symbol")
        if symbol and "." not in symbol and len(symbol) <= 5:
            return symbol
        return None
    except Exception as e:
        logger.debug("ticker_resolve_failed", company=company_name, error=str(e))
        return None


def _enrich_from_yfinance(ticker: str) -> dict:
    """Pull company profile from Yahoo Finance. Returns flat dict of fields."""
    from src.ingestion.connectors.yfinance_connector import YFinanceConnector

    connector = YFinanceConnector()
    data = connector.get_company_profile(ticker)
    if data.get("error"):
        return {}
    return data


def _enrich_from_edgar(ticker: str, identity: str) -> dict:
    """Pull 10-K financials from SEC EDGAR. Returns flat dict of fields."""
    from src.ingestion.connectors.sec_edgar import SECEdgarConnector

    connector = SECEdgarConnector(identity=identity)
    data = connector.get_company_financials(ticker)
    if data.get("error"):
        return {}
    return data


async def _enrich_from_fmp(ticker: str, api_key: str) -> dict:
    """Pull financials from FMP. Returns flat dict of fields."""
    from src.ingestion.connectors.fmp import FMPConnector

    connector = FMPConnector(api_key=api_key)
    try:
        data = await connector.fetch_financials(ticker)
        return data
    finally:
        await connector.close()


def _merge_field(
    raw: CompanyRaw,
    attr: str,
    value,
    result: EnrichmentResult,
    source: str,
) -> None:
    """Set a field on CompanyRaw if the new value is non-None and positive (for numerics)."""
    if value is None:
        return
    if isinstance(value, (int, float)) and value <= 0:
        return
    setattr(raw, attr, value)
    result.fields_updated.append(f"{attr} ({source})")


def _apply_enrichment(
    raw: CompanyRaw,
    edgar_data: dict,
    fmp_data: dict,
    yf_data: dict,
    result: EnrichmentResult,
) -> None:
    """Merge enrichment data into a CompanyRaw record.

    Priority: EDGAR > FMP > yfinance. Only overwrite LLM estimates;
    never downgrade a field that already has real data from a higher-priority source.
    """
    # Revenue
    revenue = (
        edgar_data.get("revenue")
        or fmp_data.get("revenue")
        or yf_data.get("revenue")
    )
    rev_source = (
        "edgar" if edgar_data.get("revenue")
        else "fmp" if fmp_data.get("revenue")
        else "yfinance" if yf_data.get("revenue")
        else None
    )
    if revenue and rev_source:
        _merge_field(raw, "estimated_revenue", float(revenue), result, rev_source)

    # EBITDA
    ebitda = (
        edgar_data.get("ebitda")
        or fmp_data.get("ebitda")
        or yf_data.get("ebitda")
    )
    ebitda_source = (
        "edgar" if edgar_data.get("ebitda")
        else "fmp" if fmp_data.get("ebitda")
        else "yfinance" if yf_data.get("ebitda")
        else None
    )
    if ebitda and ebitda_source:
        _merge_field(raw, "estimated_ebitda", float(ebitda), result, ebitda_source)

    # Employee count
    employees = (
        fmp_data.get("employee_count")
        or yf_data.get("employee_count")
    )
    emp_source = (
        "fmp" if fmp_data.get("employee_count")
        else "yfinance" if yf_data.get("employee_count")
        else None
    )
    if employees and emp_source:
        _merge_field(raw, "employee_count", int(employees), result, emp_source)

    # Description (prefer longer/richer descriptions)
    desc = yf_data.get("description") or fmp_data.get("description")
    desc_source = (
        "yfinance" if yf_data.get("description")
        else "fmp" if fmp_data.get("description")
        else None
    )
    if desc and desc_source and (not raw.description or len(desc) > len(raw.description)):
        _merge_field(raw, "description", desc, result, desc_source)

    # Industry
    industry = (
        fmp_data.get("industry")
        or yf_data.get("industry")
    )
    ind_source = (
        "fmp" if fmp_data.get("industry")
        else "yfinance" if yf_data.get("industry")
        else None
    )
    if industry and ind_source and not raw.industry:
        _merge_field(raw, "industry", industry, result, ind_source)


async def _enrich_from_usaspending(company_name: str) -> dict:
    """Pull federal contract data from USAspending.gov."""
    from src.ingestion.connectors.usaspending import USASpendingConnector

    connector = USASpendingConnector()
    try:
        profile = await connector.get_recipient_profile(company_name)
        if profile and profile.total_federal_spending > 0:
            return {
                "federal_contract_total": profile.total_federal_spending,
                "federal_contract_count": profile.contract_count,
                "naics_codes": profile.top_naics,
                "state": profile.state,
            }
        return {}
    finally:
        await connector.close()


async def _enrich_from_job_postings(
    company_name: str, domain: str | None
) -> dict:
    """Pull hiring signals from public job boards."""
    from src.ingestion.connectors.job_postings import JobPostingsConnector

    connector = JobPostingsConnector()
    try:
        summary = await connector.fetch_for_company(company_name, domain)
        if summary and summary.total_open_positions > 0:
            return {
                "open_positions": summary.total_open_positions,
                "hiring_departments": summary.department_breakdown,
                "executive_searches": summary.executive_searches,
            }
        return {}
    finally:
        await connector.close()


async def enrich_companies(
    companies: list[CompanyRaw],
    fmp_api_key: str = "",
    edgar_identity: str = "deal-sourcing-poc research@example.com",
    on_progress: Callable[[str], None] | None = None,
) -> list[EnrichmentResult]:
    """Enrich a list of CompanyRaw records with free public-company data.

    Sources (in priority order for financials):
      1. SEC EDGAR 10-K (audited)
      2. FMP (aggregated, needs free key)
      3. yfinance (Yahoo Finance)
    Plus non-financial signals:
      4. USAspending.gov (federal contracts as revenue proxy)
      5. Job board APIs (hiring velocity signals)

    Returns one EnrichmentResult per company describing what was updated.
    """
    results: list[EnrichmentResult] = []

    for i, raw in enumerate(companies):
        result = EnrichmentResult(company_name=raw.name)
        msg = f"Enriching {i + 1}/{len(companies)}: {raw.name}"
        if on_progress:
            on_progress(msg)
        logger.info("enrichment.start", company=raw.name)

        # ── Financial data (ticker-based) ────────────────────────────
        ticker = _resolve_ticker(raw.name)
        if ticker:
            result.ticker = ticker
            logger.info("enrichment.ticker_resolved", company=raw.name, ticker=ticker)

            yf_data: dict = {}
            edgar_data: dict = {}
            fmp_data: dict = {}

            try:
                yf_data = _enrich_from_yfinance(ticker)
                if yf_data:
                    result.sources_used.append("yfinance")
            except Exception as e:
                result.errors.append(f"yfinance: {e}")

            try:
                edgar_data = _enrich_from_edgar(ticker, edgar_identity)
                if edgar_data:
                    result.sources_used.append("edgar")
            except Exception as e:
                result.errors.append(f"edgar: {e}")

            if fmp_api_key:
                try:
                    fmp_data = await _enrich_from_fmp(ticker, fmp_api_key)
                    if fmp_data:
                        result.sources_used.append("fmp")
                except Exception as e:
                    result.errors.append(f"fmp: {e}")

            _apply_enrichment(raw, edgar_data, fmp_data, yf_data, result)

        # ── USAspending (works for private companies too) ────────────
        try:
            usa_data = await _enrich_from_usaspending(raw.name)
            if usa_data:
                result.sources_used.append("usaspending")
                if usa_data.get("federal_contract_total"):
                    _merge_field(
                        raw, "estimated_revenue",
                        usa_data["federal_contract_total"],
                        result, "usaspending",
                    )
                if usa_data.get("naics_codes") and not raw.naics_code:
                    raw.naics_code = usa_data["naics_codes"][0]
                    result.fields_updated.append("naics_code (usaspending)")
        except Exception as e:
            result.errors.append(f"usaspending: {e}")

        # ── Job postings (works for any company with a careers page) ─
        try:
            job_data = await _enrich_from_job_postings(raw.name, raw.domain)
            if job_data:
                result.sources_used.append("job_postings")
                if job_data.get("open_positions"):
                    result.fields_updated.append(
                        f"open_positions={job_data['open_positions']} (job_postings)"
                    )
                if job_data.get("executive_searches"):
                    result.fields_updated.append(
                        f"executive_searches={job_data['executive_searches']} (job_postings)"
                    )
                # Store hiring data on the raw record for downstream use.
                # CompanyRaw doesn't have these fields, so we stash them as
                # extra attributes that the normalizer can optionally pick up.
                raw.__dict__["_hiring_data"] = job_data
        except Exception as e:
            result.errors.append(f"job_postings: {e}")

        if result.fields_updated:
            logger.info(
                "enrichment.complete",
                company=raw.name,
                ticker=result.ticker,
                fields=result.fields_updated,
                sources=result.sources_used,
            )
        else:
            logger.debug("enrichment.no_updates", company=raw.name, ticker=result.ticker)

        results.append(result)

    return results
