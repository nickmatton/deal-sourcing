"""Deterministic mandate gating — eliminates ~95% of universe cheaply."""

import structlog

from src.common.schemas.ingestion import CompanyNormalized
from src.thesis_matching.thesis_schema import InvestmentThesis

logger = structlog.get_logger("thesis_matching")


def apply_hard_filters(
    company: CompanyNormalized,
    thesis: InvestmentThesis,
) -> tuple[bool, list[str]]:
    """Check if a company passes the thesis hard filters.

    Returns (passes, list_of_gap_flags).
    """
    gaps = []

    # Revenue range check
    if company.estimated_revenue_usd is not None:
        rev = company.estimated_revenue_usd
        if rev < thesis.revenue_range[0]:
            gaps.append(f"Revenue ${rev:,.0f} below thesis minimum ${thesis.revenue_range[0]:,.0f}")
        if rev > thesis.revenue_range[1]:
            gaps.append(f"Revenue ${rev:,.0f} above thesis maximum ${thesis.revenue_range[1]:,.0f}")

    # Geography check
    if thesis.geography and company.hq_country:
        if company.hq_country not in thesis.geography:
            gaps.append(f"Geography {company.hq_country} not in thesis: {thesis.geography}")

    # Ownership preference check
    if thesis.ownership_preference:
        ownership_str = company.ownership_type.value
        if ownership_str not in thesis.ownership_preference:
            gaps.append(f"Ownership type '{ownership_str}' not preferred: {thesis.ownership_preference}")

    # EBITDA margin floor
    if company.ebitda_margin is not None and thesis.ebitda_margin_floor > 0:
        if company.ebitda_margin < thesis.ebitda_margin_floor:
            gaps.append(
                f"EBITDA margin {company.ebitda_margin:.1%} below floor {thesis.ebitda_margin_floor:.1%}"
            )

    # Anti-pattern check (keyword-based)
    for pattern in thesis.anti_patterns:
        if "declining revenue" in pattern.lower():
            if (
                company.estimated_revenue_usd is not None
                and company.estimated_revenue_usd < 0
            ):
                gaps.append(f"Anti-pattern detected: {pattern}")

    passes = len(gaps) == 0
    return passes, gaps


def filter_universe(
    companies: list[CompanyNormalized],
    thesis: InvestmentThesis,
) -> tuple[list[CompanyNormalized], list[tuple[CompanyNormalized, list[str]]]]:
    """Apply hard filters to a universe of companies.

    Returns (passing_companies, rejected_with_reasons).
    """
    passing = []
    rejected = []

    for company in companies:
        passes, gaps = apply_hard_filters(company, thesis)
        if passes:
            passing.append(company)
        else:
            rejected.append((company, gaps))

    logger.info(
        "hard_filter.complete",
        thesis_id=thesis.id,
        total=len(companies),
        passing=len(passing),
        rejected=len(rejected),
    )
    return passing, rejected
