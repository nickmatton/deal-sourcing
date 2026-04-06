from pydantic import BaseModel, Field


class TriggerReason(BaseModel):
    signal: str  # e.g. "succession_risk", "hiring_freeze", "advisor_engagement"
    description: str  # human-readable explanation
    confidence: float  # 0-1
    source: str | None = None  # data source that triggered this


class DealSignal(BaseModel):
    """Output of Stage 1: Deal Signal Detection."""

    entity_id: str
    company_name: str
    sell_probability: float  # 0-1, calibrated
    trigger_reasons: list[TriggerReason] = Field(default_factory=list)
    estimated_revenue_range: tuple[float, float] | None = None  # (low, high) USD
    estimated_ebitda_range: tuple[float, float] | None = None  # (low, high) USD
    signal_freshness: str | None = None  # ISO 8601
    mandate_pass: bool = True
    score_version: str = "v1"
    scored_at: str  # ISO 8601


class ThesisMatch(BaseModel):
    """Output of Stage 2: Thesis Matching."""

    entity_id: str
    company_name: str
    thesis_id: str
    fit_score: float  # 0-100
    fit_rationale: str  # LLM-generated explanation
    gap_flags: list[str] = Field(default_factory=list)
    sector_relative_value: str | None = None  # "above", "below", "at"
    rank: int
    sell_probability: float
    scored_at: str  # ISO 8601
