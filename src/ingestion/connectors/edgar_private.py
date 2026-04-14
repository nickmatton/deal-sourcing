"""SEC EDGAR connectors for private company data.

Three free data sources, no API key required:

  1. EFTS 8-K search — finds M&A filings and extracts deal values / target
     names via NLP on the filing text.
  2. Form D search — discovers private placements with structured XML data
     including offering amounts, investor counts, and revenue ranges.
  3. Submissions API — resolves CIKs and pulls company metadata.

Rate limit: 10 req/sec, User-Agent header required.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord

logger = structlog.get_logger("ingestion.edgar_private")

USER_AGENT = "DealSourcingPipeline research@dealsourcing.dev"
EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# 8-K item numbers relevant to M&A
MA_ITEMS = {"1.01", "2.01"}

# NLP patterns for extracting deal values from 8-K text
DEAL_VALUE_PATTERNS = [
    re.compile(
        r"(?:aggregate|total)\s+(?:merger\s+)?consideration\s+(?:of\s+)?(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"purchase\s+price\s+(?:of\s+)?(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:for|at)\s+(?:a\s+)?(?:total\s+)?(?:purchase\s+)?price\s+of\s+(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"enterprise\s+value\s+(?:of\s+)?(?:approximately\s+)?"
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?",
        re.IGNORECASE,
    ),
]

TARGET_NAME_PATTERNS = [
    re.compile(
        r"(?:acqui(?:red?|sition)\s+(?:of\s+)?(?:all\s+)?(?:(?:the\s+)?(?:outstanding\s+)?"
        r"(?:shares?|stock|equity|assets?|membership\s+interests?)\s+(?:of|in)\s+)?)"
        r"([A-Z][A-Za-z0-9\s&,.'()-]{2,60}?)(?:\s*[,(]|\s+for\s+|\s+in\s+a\s+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"merger\s+(?:with|of)\s+([A-Z][A-Za-z0-9\s&,.'()-]{2,60}?)(?:\s*[,(]|\s+pursuant)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:to\s+acquire|completed\s+(?:its\s+)?acquisition\s+of)\s+"
        r"([A-Z][A-Za-z0-9\s&,.'()-]{2,60}?)(?:\s*[,(]|\s+for\s+|\s*\.)",
        re.IGNORECASE,
    ),
]

MULTIPLIER_MAP = {
    "billion": 1_000_000_000,
    "million": 1_000_000,
    "thousand": 1_000,
}


def _parse_deal_value(text: str) -> float | None:
    for pattern in DEAL_VALUE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw_num = m.group(1).replace(",", "")
            try:
                value = float(raw_num)
            except ValueError:
                continue
            unit = (m.group(2) or "").lower()
            multiplier = MULTIPLIER_MAP.get(unit, 1)
            return value * multiplier
    return None


def _parse_target_name(text: str) -> str | None:
    for pattern in TARGET_NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = m.group(1).strip().rstrip(",.")
            if len(name) > 3 and not name.isupper():
                return name
            if len(name) > 3:
                return name.title()
    return None


def _clean_html(html: str) -> str:
    """Strip HTML tags for NLP extraction."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@dataclass
class EdgarMADeal:
    """Structured M&A deal extracted from an 8-K filing."""

    acquirer_name: str
    acquirer_cik: str
    target_name: str | None = None
    enterprise_value: float | None = None
    filing_date: str = ""
    accession_number: str = ""
    items: list[str] = field(default_factory=list)
    sic_code: str | None = None
    state: str | None = None


@dataclass
class FormDOffering:
    """Structured private placement from a Form D filing."""

    issuer_name: str
    cik: str
    total_offering_amount: float | None = None
    total_amount_sold: float | None = None
    total_remaining: float | None = None
    num_investors_accredited: int | None = None
    num_investors_non_accredited: int | None = None
    industry_group: str | None = None
    revenue_range: str | None = None
    exemptions: list[str] = field(default_factory=list)
    filing_date: str = ""
    state: str | None = None
    related_persons: list[dict] = field(default_factory=list)


class EdgarPrivateConnector:
    """Discovers private company data from SEC EDGAR free APIs.

    Sources:
      - 8-K full-text search for M&A filings (deal values, target names)
      - Form D structured XML for private placements
      - Submissions API for company metadata
    """

    def __init__(self, identity: str = USER_AGENT) -> None:
        self._identity = identity
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": identity},
            follow_redirects=True,
        )
        self._rate_delay = 0.15  # ~6 req/sec, well under 10 limit

    async def _get(self, url: str, **params) -> dict | str:
        await asyncio.sleep(self._rate_delay)
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return resp.json()
        return resp.text

    # ─── 8-K M&A Filing Search ───────────────────────────────────────

    async def search_ma_filings(
        self,
        start_date: str = "2023-01-01",
        end_date: str | None = None,
        sector_sic: str | None = None,
        max_results: int = 200,
    ) -> list[EdgarMADeal]:
        """Search 8-K filings for M&A transactions with deal value extraction."""
        if end_date is None:
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        queries = [
            '"total consideration" "acquisition"',
            '"purchase price" "acquisition"',
            '"enterprise value" "definitive agreement"',
            '"merger agreement" "aggregate consideration"',
        ]

        all_hits: dict[str, dict] = {}
        for query in queries:
            offset = 0
            while offset < max_results:
                batch_size = min(100, max_results - offset)
                try:
                    data = await self._get(
                        EFTS_BASE,
                        q=query,
                        forms="8-K",
                        dateRange="custom",
                        startdt=start_date,
                        enddt=end_date,
                        size=batch_size,
                        **{"from": offset},
                    )
                    if not isinstance(data, dict):
                        break
                    hits = data.get("hits", {}).get("hits", [])
                    if not hits:
                        break
                    for hit in hits:
                        adsh = hit.get("_source", {}).get("adsh", "")
                        if adsh and adsh not in all_hits:
                            all_hits[adsh] = hit
                    offset += batch_size
                    if len(hits) < batch_size:
                        break
                except httpx.HTTPStatusError as e:
                    logger.warning("efts_search_error", query=query, error=str(e))
                    break

        logger.info("ma_search_complete", unique_filings=len(all_hits))

        deals: list[EdgarMADeal] = []
        for adsh, hit in list(all_hits.items())[:max_results]:
            src = hit.get("_source", {})
            items = src.get("items", [])

            if not any(item in MA_ITEMS for item in items):
                continue

            names = src.get("display_names", [])
            acquirer_name = names[0].split("(")[0].strip() if names else "Unknown"
            ciks = src.get("ciks", [])
            acquirer_cik = ciks[0] if ciks else ""

            deal = EdgarMADeal(
                acquirer_name=acquirer_name,
                acquirer_cik=acquirer_cik,
                filing_date=src.get("file_date", ""),
                accession_number=adsh,
                items=items,
                sic_code=(src.get("sics") or [None])[0],
                state=(src.get("biz_states") or [None])[0],
            )

            # Fetch filing text for NLP extraction
            try:
                filing_text = await self._fetch_filing_text(acquirer_cik, adsh)
                if filing_text:
                    deal.enterprise_value = _parse_deal_value(filing_text)
                    deal.target_name = _parse_target_name(filing_text)
            except Exception as e:
                logger.debug("filing_text_error", adsh=adsh, error=str(e))

            if deal.target_name or deal.enterprise_value:
                deals.append(deal)

        logger.info("ma_deals_extracted", deals=len(deals))
        return deals

    async def _fetch_filing_text(self, cik: str, accession: str) -> str | None:
        """Fetch the primary document text from an 8-K filing."""
        accession_clean = accession.replace("-", "")
        index_url = f"{ARCHIVES_BASE}/{cik}/{accession_clean}/index.json"
        try:
            index = await self._get(index_url)
            if not isinstance(index, dict):
                return None
            items = index.get("directory", {}).get("item", [])
            primary = None
            for item in items:
                name = item.get("name", "")
                if name.endswith(".htm") or name.endswith(".html"):
                    if "ex" not in name.lower()[:3]:
                        primary = name
                        break
            if not primary and items:
                for item in items:
                    name = item.get("name", "")
                    if name.endswith(".htm") or name.endswith(".html"):
                        primary = name
                        break
            if not primary:
                return None

            doc_url = f"{ARCHIVES_BASE}/{cik}/{accession_clean}/{primary}"
            html = await self._get(doc_url)
            if isinstance(html, str):
                return _clean_html(html)[:50_000]
        except Exception as e:
            logger.debug("fetch_filing_error", cik=cik, adsh=accession, error=str(e))
        return None

    # ─── Form D Private Placement Search ─────────────────────────────

    async def search_form_d(
        self,
        start_date: str = "2023-01-01",
        end_date: str | None = None,
        max_results: int = 200,
    ) -> list[FormDOffering]:
        """Search Form D filings for private placement data."""
        if end_date is None:
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        all_hits: dict[str, dict] = {}
        offset = 0
        while offset < max_results:
            batch_size = min(100, max_results - offset)
            try:
                data = await self._get(
                    EFTS_BASE,
                    q="*",
                    forms="D",
                    dateRange="custom",
                    startdt=start_date,
                    enddt=end_date,
                    size=batch_size,
                    **{"from": offset},
                )
                if not isinstance(data, dict):
                    break
                hits = data.get("hits", {}).get("hits", [])
                if not hits:
                    break
                for hit in hits:
                    adsh = hit.get("_source", {}).get("adsh", "")
                    if adsh and adsh not in all_hits:
                        all_hits[adsh] = hit
                offset += batch_size
                if len(hits) < batch_size:
                    break
            except httpx.HTTPStatusError as e:
                logger.warning("efts_form_d_error", error=str(e))
                break

        logger.info("form_d_search_hits", unique_filings=len(all_hits))

        offerings: list[FormDOffering] = []
        for adsh, hit in list(all_hits.items())[:max_results]:
            src = hit.get("_source", {})
            ciks = src.get("ciks", [])
            cik = ciks[0] if ciks else ""
            names = src.get("display_names", [])
            name = names[0].split("(")[0].strip() if names else "Unknown"

            offering = await self._parse_form_d_xml(cik, adsh, name)
            if offering:
                offering.filing_date = src.get("file_date", "")
                offering.state = (src.get("biz_states") or [None])[0]
                offerings.append(offering)

        logger.info("form_d_search_complete", offerings=len(offerings))
        return offerings

    async def _parse_form_d_xml(
        self, cik: str, accession: str, fallback_name: str
    ) -> FormDOffering | None:
        """Fetch and parse Form D XML for structured offering data."""
        accession_clean = accession.replace("-", "")
        index_url = f"{ARCHIVES_BASE}/{cik}/{accession_clean}/index.json"

        try:
            index = await self._get(index_url)
            if not isinstance(index, dict):
                return None

            items = index.get("directory", {}).get("item", [])
            xml_file = None
            for item in items:
                name = item.get("name", "")
                if name.endswith(".xml") and "primary" in name.lower():
                    xml_file = name
                    break
            if not xml_file:
                for item in items:
                    name = item.get("name", "")
                    if name.endswith(".xml"):
                        xml_file = name
                        break

            if not xml_file:
                return FormDOffering(issuer_name=fallback_name, cik=cik)

            xml_url = f"{ARCHIVES_BASE}/{cik}/{accession_clean}/{xml_file}"
            xml_text = await self._get(xml_url)
            if not isinstance(xml_text, str):
                return FormDOffering(issuer_name=fallback_name, cik=cik)

            return self._extract_form_d_fields(xml_text, cik, fallback_name)

        except Exception as e:
            logger.debug("form_d_parse_error", cik=cik, adsh=accession, error=str(e))
            return FormDOffering(issuer_name=fallback_name, cik=cik)

    @staticmethod
    def _extract_form_d_fields(
        xml_text: str, cik: str, fallback_name: str
    ) -> FormDOffering:
        """Extract structured fields from Form D XML."""
        offering = FormDOffering(issuer_name=fallback_name, cik=cik)

        try:
            xml_text = re.sub(r'\s+xmlns[^"]*"[^"]*"', "", xml_text, count=1)
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return offering

        def _find_text(path: str) -> str | None:
            el = root.find(f".//{path}")
            return el.text.strip() if el is not None and el.text else None

        def _find_float(path: str) -> float | None:
            val = _find_text(path)
            if val:
                try:
                    return float(val.replace(",", ""))
                except ValueError:
                    pass
            return None

        def _find_int(path: str) -> int | None:
            val = _find_text(path)
            if val:
                try:
                    return int(val.replace(",", ""))
                except ValueError:
                    pass
            return None

        name = _find_text("issuer/entityName") or _find_text("primaryIssuer/entityName")
        if name:
            offering.issuer_name = name

        offering.total_offering_amount = _find_float("offeringData/offeringSalesAmounts/totalOfferingAmount")
        offering.total_amount_sold = _find_float("offeringData/offeringSalesAmounts/totalAmountSold")
        offering.total_remaining = _find_float("offeringData/offeringSalesAmounts/totalRemaining")
        offering.num_investors_accredited = _find_int("offeringData/investors/hasNonAccreditedInvestors/../accreditedNumber")
        offering.num_investors_non_accredited = _find_int("offeringData/investors/nonAccreditedNumber")
        offering.industry_group = _find_text("issuer/industryGroup/industryGroupType")
        offering.revenue_range = _find_text("issuer/issuerSize/revenueRange")

        exemptions = root.findall(".//federalExemptionsExclusions/item")
        offering.exemptions = [e.text.strip() for e in exemptions if e.text]

        for person in root.findall(".//relatedPersonInfo/relatedPersonInfo"):
            p: dict = {}
            for child in person:
                if child.text and child.text.strip():
                    p[child.tag] = child.text.strip()
            if p:
                offering.related_persons.append(p)

        return offering

    # ─── Submissions API — Company Metadata ──────────────────────────

    async def get_company_metadata(self, cik: str) -> dict:
        """Fetch company metadata from the submissions API."""
        padded = cik.zfill(10)
        url = f"{SUBMISSIONS_BASE}/CIK{padded}.json"
        data = await self._get(url)
        if not isinstance(data, dict):
            return {}
        return {
            "cik": data.get("cik"),
            "name": data.get("name"),
            "entity_type": data.get("entityType"),
            "sic": data.get("sic"),
            "sic_description": data.get("sicDescription"),
            "tickers": data.get("tickers", []),
            "exchanges": data.get("exchanges", []),
            "ein": data.get("ein"),
            "state_of_incorporation": data.get("stateOfIncorporation"),
            "fiscal_year_end": data.get("fiscalYearEnd"),
            "category": data.get("category"),
            "addresses": data.get("addresses", {}),
            "phone": data.get("phone"),
        }

    # ─── Conversion to Pipeline Schemas ──────────────────────────────

    def deals_to_transactions(self, deals: list[EdgarMADeal]) -> list[TransactionRecord]:
        """Convert extracted M&A deals to TransactionRecord schema."""
        records = []
        for deal in deals:
            records.append(TransactionRecord(
                transaction_id=f"edgar-8k-{deal.accession_number}",
                target_name=deal.target_name or "Unknown Target",
                buyer_name=deal.acquirer_name,
                buyer_type="strategic",
                deal_type="acquisition",
                sector=deal.sic_code,
                enterprise_value=deal.enterprise_value,
                deal_date=deal.filing_date,
                geography=deal.state,
                source="sec_edgar_8k",
            ))
        return records

    def offerings_to_companies(self, offerings: list[FormDOffering]) -> list[CompanyRaw]:
        """Convert Form D offerings to CompanyRaw schema."""
        records = []
        for off in offerings:
            revenue = None
            if off.revenue_range:
                revenue = self._parse_revenue_range(off.revenue_range)

            records.append(CompanyRaw(
                source="sec_edgar_form_d",
                source_id=f"cik-{off.cik}",
                name=off.issuer_name,
                industry=off.industry_group,
                hq_state=off.state,
                hq_country="US",
                estimated_revenue=revenue,
                ownership_type=OwnershipType.VC_BACKED if off.exemptions else OwnershipType.UNKNOWN,
                funding_total=off.total_amount_sold,
                executives=[p for p in off.related_persons],
            ))
        return records

    @staticmethod
    def _parse_revenue_range(range_str: str) -> float | None:
        """Parse Form D revenue range strings like 'No Revenues', '$1-$5 million'."""
        if not range_str or "no revenue" in range_str.lower() or "decline" in range_str.lower():
            return None
        numbers = re.findall(r"\$([\d,.]+)", range_str)
        if not numbers:
            return None
        values = [float(n.replace(",", "")) for n in numbers]
        multiplier = 1_000_000 if "million" in range_str.lower() else 1
        if "billion" in range_str.lower():
            multiplier = 1_000_000_000
        if len(values) >= 2:
            return ((values[0] + values[1]) / 2) * multiplier
        return values[0] * multiplier

    async def close(self) -> None:
        await self._client.aclose()
