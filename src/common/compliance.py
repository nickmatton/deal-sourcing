from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class LegalBasis(StrEnum):
    LEGITIMATE_INTEREST = "legitimate_interest"
    CONSENT = "consent"
    CONTRACTUAL = "contractual"


class DataRetentionPolicy(StrEnum):
    FUND_LIFE = "fund_life"  # Retain for fund life + 7 years
    ACTIVE_PIPELINE = "active_pipeline"  # Retain while in active pipeline
    NINETY_DAYS = "90_days"  # Short-term retention for outreach
    INDEFINITE = "indefinite"  # Anonymized/aggregated data


class PersonalDataTag(BaseModel):
    field_name: str
    legal_basis: LegalBasis
    retention_policy: DataRetentionPolicy
    consent_timestamp: str | None = None
    expiry_date: str | None = None
    purpose: str


class ComplianceRecord(BaseModel):
    entity_id: str
    data_tags: list[PersonalDataTag] = Field(default_factory=list)
    gdpr_applicable: bool = False
    can_spam_applicable: bool = True
    outreach_unsubscribed: bool = False
    dsar_requests: list[dict] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ComplianceChecker:
    """Validates data operations against compliance rules."""

    @staticmethod
    def can_send_outreach(record: ComplianceRecord) -> tuple[bool, str]:
        if record.outreach_unsubscribed:
            return False, "Contact has unsubscribed from outreach"
        if record.gdpr_applicable:
            has_basis = any(
                t.legal_basis in (LegalBasis.LEGITIMATE_INTEREST, LegalBasis.CONSENT)
                for t in record.data_tags
            )
            if not has_basis:
                return False, "No valid legal basis for GDPR-covered contact"
        return True, "OK"

    @staticmethod
    def check_data_retention(record: ComplianceRecord) -> list[PersonalDataTag]:
        """Return tags whose data has expired and should be purged."""
        now = datetime.now(timezone.utc).isoformat()
        expired = []
        for tag in record.data_tags:
            if tag.expiry_date and tag.expiry_date < now:
                expired.append(tag)
        return expired

    @staticmethod
    def handle_dsar(record: ComplianceRecord) -> dict:
        """Generate a data subject access request response."""
        return {
            "entity_id": record.entity_id,
            "data_held": [t.model_dump() for t in record.data_tags],
            "gdpr_applicable": record.gdpr_applicable,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
