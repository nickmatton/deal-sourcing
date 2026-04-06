from enum import StrEnum

from pydantic import BaseModel, Field


class OutreachChannel(StrEnum):
    WARM_INTRO = "warm_intro"
    EMAIL = "email"
    LINKEDIN = "linkedin"
    PHONE = "phone"
    EVENT = "event"


class PipelineStage(StrEnum):
    SOURCED = "sourced"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    MEETING = "meeting"
    NDA = "nda"
    LOI = "loi"
    DILIGENCE = "diligence"
    CLOSED = "closed"
    PASSED = "passed"


class ToneRecommendation(StrEnum):
    PREMIUM_BUYER = "premium_buyer"
    DISCIPLINED_VALUE = "disciplined_value"
    PARTNERSHIP = "partnership"
    GROWTH_ACCELERATION = "growth_acceleration"


class FounderExpectation(BaseModel):
    """Output of Founder Price Expectation Model."""

    entity_id: str
    expected_ask_range: tuple[float, float]  # (low, high) USD
    tone_recommendation: ToneRecommendation
    narrative_hooks: list[str] = Field(default_factory=list)
    seller_sophistication: float  # 0-1


class OutreachDraft(BaseModel):
    """LLM-generated outreach draft for human review."""

    entity_id: str
    company_name: str
    thesis_id: str
    channel: OutreachChannel
    subject: str | None = None
    body: str
    warm_path: list[str] = Field(default_factory=list)  # names in intro chain
    tone: ToneRecommendation
    approved: bool = False
    edited_body: str | None = None


class OutreachEvent(BaseModel):
    """Tracks an outreach attempt and its outcome."""

    entity_id: str
    channel: OutreachChannel
    sent_at: str  # ISO 8601
    opened: bool = False
    replied: bool = False
    meeting_scheduled: bool = False
    reply_sentiment: str | None = None  # positive, neutral, negative
    next_action: str | None = None
    pipeline_stage: PipelineStage = PipelineStage.CONTACTED
