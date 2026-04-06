import re

import structlog

from src.common.schemas.ingestion import CompanyNormalized, CompanyRaw

logger = structlog.get_logger("ingestion.normalizer")


def normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.rstrip("/")
    return domain


def normalize_country(country: str | None) -> str:
    if not country:
        return "US"
    country = country.strip().upper()
    country_map = {
        "UNITED STATES": "US",
        "USA": "US",
        "UNITED KINGDOM": "GB",
        "UK": "GB",
        "CANADA": "CA",
    }
    return country_map.get(country, country)


def normalize_company(
    raw: CompanyRaw, entity_id: str
) -> CompanyNormalized:
    ebitda_margin = None
    if raw.estimated_ebitda and raw.estimated_revenue and raw.estimated_revenue > 0:
        ebitda_margin = raw.estimated_ebitda / raw.estimated_revenue

    return CompanyNormalized(
        entity_id=entity_id,
        name=raw.name.strip(),
        domain=normalize_domain(raw.domain),
        description=raw.description,
        industry_primary=raw.industry,
        naics_code=raw.naics_code,
        hq_city=raw.hq_city,
        hq_state=raw.hq_state,
        hq_country=normalize_country(raw.hq_country),
        founded_year=raw.founded_year,
        employee_count=raw.employee_count,
        estimated_revenue_usd=raw.estimated_revenue,
        estimated_ebitda_usd=raw.estimated_ebitda,
        ebitda_margin=ebitda_margin,
        ownership_type=raw.ownership_type,
        funding_total_usd=raw.funding_total,
        source_records=[f"{raw.source}:{raw.source_id}"],
        data_freshness=raw.ingested_at,
    )
