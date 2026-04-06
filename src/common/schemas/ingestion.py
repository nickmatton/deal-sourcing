from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DataTier(StrEnum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class OwnershipType(StrEnum):
    FOUNDER = "founder"
    FAMILY = "family"
    PE_BACKED = "pe_backed"
    PUBLIC = "public"
    VC_BACKED = "vc_backed"
    UNKNOWN = "unknown"


class CompanyRaw(BaseModel):
    """Bronze-tier company record from a single source."""

    source: str
    source_id: str
    name: str
    domain: str | None = None
    description: str | None = None
    industry: str | None = None
    naics_code: str | None = None
    hq_city: str | None = None
    hq_state: str | None = None
    hq_country: str | None = None
    founded_year: int | None = None
    employee_count: int | None = None
    estimated_revenue: float | None = None
    estimated_ebitda: float | None = None
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    funding_total: float | None = None
    last_funding_date: str | None = None
    last_funding_round: str | None = None
    executives: list[dict] = Field(default_factory=list)
    ingested_at: str = Field(
        default_factory=lambda: datetime.now().isoformat()
    )


class CompanyNormalized(BaseModel):
    """Silver-tier company record — cleaned and standardized."""

    entity_id: str
    name: str
    domain: str | None = None
    description: str | None = None
    industry_primary: str | None = None
    naics_code: str | None = None
    hq_city: str | None = None
    hq_state: str | None = None
    hq_country: str = "US"
    founded_year: int | None = None
    employee_count: int | None = None
    estimated_revenue_usd: float | None = None
    estimated_ebitda_usd: float | None = None
    ebitda_margin: float | None = None
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    funding_total_usd: float | None = None
    source_records: list[str] = Field(default_factory=list)
    data_freshness: str | None = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat()
    )


class TransactionRecord(BaseModel):
    """Historical M&A/PE transaction for training and comps."""

    transaction_id: str
    target_entity_id: str | None = None
    target_name: str
    buyer_name: str | None = None
    buyer_type: str | None = None  # pe, strategic, etc.
    deal_type: str | None = None  # lbo, growth, add-on, merger
    sector: str | None = None
    enterprise_value: float | None = None
    ev_ebitda_multiple: float | None = None
    ev_revenue_multiple: float | None = None
    target_revenue: float | None = None
    target_ebitda: float | None = None
    target_ebitda_margin: float | None = None
    target_revenue_growth: float | None = None
    deal_date: str
    geography: str | None = None
    source: str
