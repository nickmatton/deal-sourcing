"""Tests for thesis hard filter."""

from src.common.schemas.ingestion import CompanyNormalized, OwnershipType
from src.thesis_matching.hard_filter import apply_hard_filters
from src.thesis_matching.thesis_schema import InvestmentThesis


def _make_thesis() -> InvestmentThesis:
    return InvestmentThesis(
        id="test-thesis",
        description="Test thesis",
        sector=["software"],
        revenue_range=(5_000_000, 50_000_000),
        ebitda_margin_floor=0.15,
        geography=["US", "CA"],
        ownership_preference=["founder", "family"],
        growth_floor=0.05,
    )


def _make_company(**kwargs) -> CompanyNormalized:
    defaults = dict(
        entity_id="test-id",
        name="Test Company",
        hq_country="US",
        estimated_revenue_usd=20_000_000,
        estimated_ebitda_usd=4_000_000,
        ebitda_margin=0.20,
        ownership_type=OwnershipType.FOUNDER,
    )
    defaults.update(kwargs)
    return CompanyNormalized(**defaults)


class TestHardFilter:
    def test_passing_company(self):
        thesis = _make_thesis()
        company = _make_company()
        passes, gaps = apply_hard_filters(company, thesis)
        assert passes
        assert len(gaps) == 0

    def test_revenue_too_low(self):
        thesis = _make_thesis()
        company = _make_company(estimated_revenue_usd=1_000_000)
        passes, gaps = apply_hard_filters(company, thesis)
        assert not passes
        assert any("below thesis minimum" in g for g in gaps)

    def test_revenue_too_high(self):
        thesis = _make_thesis()
        company = _make_company(estimated_revenue_usd=100_000_000)
        passes, gaps = apply_hard_filters(company, thesis)
        assert not passes
        assert any("above thesis maximum" in g for g in gaps)

    def test_wrong_geography(self):
        thesis = _make_thesis()
        company = _make_company(hq_country="GB")
        passes, gaps = apply_hard_filters(company, thesis)
        assert not passes
        assert any("Geography" in g for g in gaps)

    def test_wrong_ownership(self):
        thesis = _make_thesis()
        company = _make_company(ownership_type=OwnershipType.PE_BACKED)
        passes, gaps = apply_hard_filters(company, thesis)
        assert not passes
        assert any("Ownership" in g for g in gaps)

    def test_margin_below_floor(self):
        thesis = _make_thesis()
        company = _make_company(ebitda_margin=0.10)
        passes, gaps = apply_hard_filters(company, thesis)
        assert not passes
        assert any("margin" in g.lower() for g in gaps)
