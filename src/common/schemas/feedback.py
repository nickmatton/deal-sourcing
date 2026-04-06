from enum import StrEnum

from pydantic import BaseModel, Field


class FeedbackType(StrEnum):
    OUTREACH_OUTCOME = "outreach_outcome"
    DEAL_TEAM_DECISION = "deal_team_decision"
    PIPELINE_PROGRESSION = "pipeline_progression"
    PASS_REASON = "pass_reason"
    CLEARED_PRICE = "cleared_price"
    FOUNDER_EXPECTATION = "founder_expectation"
    POST_ACQUISITION = "post_acquisition"
    SIGNAL_EFFICACY = "signal_efficacy"


class PassReason(StrEnum):
    PRICE = "price"
    FIT = "fit"
    QUALITY = "quality"
    TIMING = "timing"
    COMPETITIVE = "competitive"
    MANAGEMENT = "management"
    MARKET = "market"
    OTHER = "other"


class OutcomeFeedback(BaseModel):
    """Captures outcomes for model retraining."""

    entity_id: str
    feedback_type: FeedbackType
    stage: str
    details: dict = Field(default_factory=dict)
    recorded_at: str  # ISO 8601


class ModelPerformanceMetric(BaseModel):
    model_name: str
    model_version: str
    metric_name: str  # e.g. "auc", "mape", "ndcg", "psi"
    metric_value: float
    threshold: float  # alert if crossed
    breached: bool = False
    measured_at: str  # ISO 8601


class RetrainingTrigger(BaseModel):
    model_name: str
    reason: str  # "scheduled", "drift_detected", "performance_degradation"
    metric: ModelPerformanceMetric | None = None
    new_samples_available: int = 0
    minimum_samples_required: int = 0
    triggered_at: str  # ISO 8601
