from enum import StrEnum

from pydantic import BaseModel, Field


class ConfidenceGrade(StrEnum):
    A = "A"  # Rich data, high-quality comps
    B = "B"  # Moderate data completeness
    C = "C"  # Sparse data, wide confidence intervals


class ValueDriver(BaseModel):
    feature: str
    direction: str  # "positive" or "negative"
    magnitude: float  # SHAP value
    description: str


class ShadowValuation(BaseModel):
    """Output of Stage 3A: Shadow Valuation Engine."""

    entity_id: str
    company_name: str
    ev_point_estimate: float  # USD
    ev_range_80ci: tuple[float, float]  # (low, high) USD
    estimated_revenue: float | None = None
    estimated_ebitda: float | None = None
    implied_ev_ebitda_multiple: float | None = None
    implied_ev_revenue_multiple: float | None = None
    key_value_drivers: list[ValueDriver] = Field(default_factory=list)
    confidence_grade: ConfidenceGrade = ConfidenceGrade.B
    illiquidity_discount_applied: float = 0.20
    data_freshness: str | None = None
    model_version: str = "v1"
    valued_at: str  # ISO 8601


class AlphaSignal(BaseModel):
    signal_type: str  # e.g. "revenue_growth_vs_multiple", "motivated_seller"
    description: str
    strength: float  # 0-1


class AlphaScore(BaseModel):
    """Output of Stage 3B: Mispricing Detection."""

    entity_id: str
    company_name: str
    alpha_score: float  # predicted fair value / shadow value - 1
    mispricing_reason: str  # LLM-generated narrative
    efficiently_priced: bool  # True = likely auction bait, no edge
    alpha_signals: list[AlphaSignal] = Field(default_factory=list)
    scored_at: str  # ISO 8601
