"""Tests for inter-stage Pydantic schemas."""

from src.common.schemas.ingestion import CompanyNormalized, CompanyRaw, OwnershipType
from src.common.schemas.signals import DealSignal, TriggerReason
from src.common.schemas.underwriting import IRRDistribution, LBOAssumptions
from src.common.schemas.valuation import ConfidenceGrade, ShadowValuation


class TestCompanySchemas:
    def test_company_raw_creation(self):
        raw = CompanyRaw(
            source="pitchbook",
            source_id="pb-123",
            name="Acme Corp",
            domain="acme.com",
            hq_country="US",
            estimated_revenue=10_000_000,
        )
        assert raw.source == "pitchbook"
        assert raw.ownership_type == OwnershipType.UNKNOWN

    def test_company_normalized_creation(self):
        norm = CompanyNormalized(
            entity_id="uuid-123",
            name="Acme Corp",
            hq_country="US",
            estimated_revenue_usd=10_000_000,
            estimated_ebitda_usd=2_000_000,
            ebitda_margin=0.20,
        )
        assert norm.ebitda_margin == 0.20


class TestSignalSchemas:
    def test_deal_signal(self):
        signal = DealSignal(
            entity_id="uuid-123",
            company_name="Acme Corp",
            sell_probability=0.72,
            trigger_reasons=[
                TriggerReason(
                    signal="founder_age",
                    description="Founder is 67 with no succession plan",
                    confidence=0.85,
                )
            ],
            scored_at="2026-01-01T00:00:00Z",
        )
        assert signal.sell_probability == 0.72
        assert len(signal.trigger_reasons) == 1
        assert signal.trigger_reasons[0].signal == "founder_age"


class TestValuationSchemas:
    def test_shadow_valuation(self):
        val = ShadowValuation(
            entity_id="uuid-123",
            company_name="Acme Corp",
            ev_point_estimate=45_000_000,
            ev_range_80ci=(32_000_000, 62_000_000),
            estimated_revenue=20_000_000,
            estimated_ebitda=4_000_000,
            implied_ev_ebitda_multiple=11.25,
            confidence_grade=ConfidenceGrade.B,
            valued_at="2026-01-01T00:00:00Z",
        )
        assert val.ev_point_estimate == 45_000_000
        assert val.confidence_grade == ConfidenceGrade.B


class TestUnderwritingSchemas:
    def test_lbo_assumptions(self):
        assumptions = LBOAssumptions(
            entry_ebitda_mean=5_000_000,
            entry_ebitda_std=500_000,
            entry_multiple_low=6.0,
            entry_multiple_mode=8.0,
            entry_multiple_high=10.0,
            revenue_growth_mean=0.10,
            revenue_growth_std=0.05,
        )
        assert assumptions.num_simulations == 10_000
        assert len(assumptions.hold_periods) == 5

    def test_irr_distribution(self):
        dist = IRRDistribution(
            p10=0.08, p25=0.15, p50=0.22, p75=0.30, p90=0.38,
            mean=0.22, std=0.10,
        )
        assert dist.p50 == 0.22
