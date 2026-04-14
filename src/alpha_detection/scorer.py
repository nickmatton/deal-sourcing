"""Alpha scoring engine — detects mispricing opportunities in acquisition targets.

Compares shadow valuation multiples against comparable transaction multiples
and evaluates structural alpha signals (operational improvement, buy-and-build,
bilateral deal probability, market timing) to produce a composite alpha score.
"""

from datetime import datetime, timezone

import numpy as np
import structlog

from src.common.audit import AuditAction, AuditEntry, AuditLogger
from src.common.logging import PipelineStage, log_stage
from src.common.schemas.ingestion import CompanyNormalized, TransactionRecord
from src.common.schemas.valuation import AlphaScore, AlphaSignal, ShadowValuation
from src.valuation.margin_estimator import SECTOR_MEDIAN_MARGINS

logger = structlog.get_logger("alpha_detection")


def _normalize_sector(sector: str | None) -> str:
    if not sector:
        return "default"
    return sector.lower().replace(" ", "_").replace("-", "_")


def _compute_comp_multiple(
    transactions: list[TransactionRecord],
    company_sector: str | None,
) -> float | None:
    """Derive a median EV/EBITDA multiple from sector-relevant comps."""
    sector_key = _normalize_sector(company_sector)
    relevant = []
    for tx in transactions:
        if tx.ev_ebitda_multiple is not None and tx.ev_ebitda_multiple > 0:
            tx_sector = _normalize_sector(tx.sector)
            if tx_sector == sector_key or sector_key == "default":
                relevant.append(tx.ev_ebitda_multiple)

    if not relevant:
        for tx in transactions:
            if tx.ev_ebitda_multiple is not None and tx.ev_ebitda_multiple > 0:
                relevant.append(tx.ev_ebitda_multiple)

    if not relevant:
        return None
    return float(np.median(relevant))


def _operational_improvement_signal(
    company: CompanyNormalized,
) -> AlphaSignal | None:
    """Detect operational improvement potential by comparing margin to sector median."""
    sector_key = _normalize_sector(company.industry_primary)
    sector_median = SECTOR_MEDIAN_MARGINS.get(sector_key, SECTOR_MEDIAN_MARGINS["default"])

    if company.ebitda_margin is not None and company.ebitda_margin < sector_median:
        gap = sector_median - company.ebitda_margin
        strength = min(gap / sector_median, 1.0)
        return AlphaSignal(
            signal_type="operational_improvement",
            description=(
                f"EBITDA margin ({company.ebitda_margin:.0%}) is {gap:.0%} below "
                f"sector median ({sector_median:.0%}), indicating operational "
                f"improvement potential under new ownership"
            ),
            strength=round(strength, 3),
        )
    return None


def _multiple_discount_signal(
    valuation: ShadowValuation,
    comp_multiple: float | None,
) -> AlphaSignal | None:
    """Detect if the implied entry multiple is below comparable transactions."""
    if comp_multiple is None or valuation.implied_ev_ebitda_multiple is None:
        return None

    discount = (comp_multiple - valuation.implied_ev_ebitda_multiple) / comp_multiple
    if discount > 0.05:
        strength = min(discount / 0.40, 1.0)
        return AlphaSignal(
            signal_type="multiple_discount",
            description=(
                f"Implied entry multiple ({valuation.implied_ev_ebitda_multiple:.1f}x) "
                f"is {discount:.0%} below comparable transaction median "
                f"({comp_multiple:.1f}x)"
            ),
            strength=round(strength, 3),
        )
    return None


def _motivated_seller_signal(
    company: CompanyNormalized,
) -> AlphaSignal | None:
    """Detect ownership structures that suggest motivated sellers."""
    current_year = datetime.now(timezone.utc).year
    signals = []

    if company.ownership_type.value == "founder" and company.founded_year:
        tenure = current_year - company.founded_year
        if tenure >= 20:
            signals.append(f"founder-owned for {tenure} years (succession pressure)")

    if company.ownership_type.value == "pe_backed":
        signals.append("PE-backed (potential hold period pressure)")

    if company.ownership_type.value == "family":
        if company.founded_year and (current_year - company.founded_year) >= 30:
            signals.append("multi-generational family business (transition risk)")

    if not signals:
        return None

    strength = min(len(signals) * 0.4, 1.0)
    return AlphaSignal(
        signal_type="motivated_seller",
        description="; ".join(signals),
        strength=round(strength, 3),
    )


def _bilateral_probability_signal(
    company: CompanyNormalized,
) -> AlphaSignal | None:
    """Estimate likelihood of a bilateral (non-auction) deal.

    Smaller, founder-owned, family, or niche businesses are more likely
    to transact bilaterally, avoiding the auction premium.
    """
    score = 0.0
    reasons = []

    if company.ownership_type.value in ("founder", "family"):
        score += 0.3
        reasons.append("owner-operator (less likely to run formal process)")

    revenue = company.estimated_revenue_usd
    if revenue is not None and revenue < 50_000_000:
        score += 0.2
        reasons.append(f"sub-$50M revenue (below typical banker threshold)")

    if company.employee_count is not None and company.employee_count < 200:
        score += 0.1
        reasons.append("small workforce (less institutional infrastructure)")

    if score < 0.2:
        return None

    return AlphaSignal(
        signal_type="bilateral_probability",
        description="; ".join(reasons),
        strength=round(min(score, 1.0), 3),
    )


def _market_timing_signal(
    company: CompanyNormalized,
    comp_multiple: float | None,
) -> AlphaSignal | None:
    """Detect if sector multiples are depressed relative to historical norms."""
    sector_key = _normalize_sector(company.industry_primary)

    historical_norms: dict[str, float] = {
        "software": 14.0,
        "saas": 16.0,
        "healthcare_it": 12.0,
        "healthcare_services": 11.0,
        "business_services": 10.0,
        "financial_services": 10.0,
        "industrials": 9.0,
        "manufacturing": 8.5,
        "consumer": 9.0,
        "technology_services": 12.0,
        "distribution": 8.0,
        "default": 10.0,
    }

    norm = historical_norms.get(sector_key, historical_norms["default"])
    if comp_multiple is None:
        return None

    discount = (norm - comp_multiple) / norm
    if discount > 0.10:
        return AlphaSignal(
            signal_type="market_timing",
            description=(
                f"Current sector multiples ({comp_multiple:.1f}x) are {discount:.0%} "
                f"below historical norms ({norm:.1f}x), suggesting a buying window"
            ),
            strength=round(min(discount / 0.30, 1.0), 3),
        )
    return None


class AlphaScorer:
    """Produces composite alpha scores for acquisition targets.

    Alpha measures the degree to which a target is mispriced relative to
    comparable transactions, adjusted for structural advantages the buyer
    may have (operational playbook, bilateral access, market timing).

    A high alpha score means the buyer is likely getting more value than
    the market price implies.  A low score (or efficiently_priced=True)
    means the target is likely to trade at fair value in a competitive
    auction with no differentiated buyer edge.
    """

    def __init__(
        self,
        efficiently_priced_threshold: float = 0.20,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._efficiently_priced_threshold = efficiently_priced_threshold
        self._audit = audit_logger or AuditLogger()

    def score(
        self,
        company: CompanyNormalized,
        valuation: ShadowValuation,
        transactions: list[TransactionRecord],
    ) -> AlphaScore:
        """Score a single company for mispricing alpha."""
        comp_multiple = _compute_comp_multiple(transactions, company.industry_primary)
        signals: list[AlphaSignal] = []

        op_signal = _operational_improvement_signal(company)
        if op_signal:
            signals.append(op_signal)

        mult_signal = _multiple_discount_signal(valuation, comp_multiple)
        if mult_signal:
            signals.append(mult_signal)

        seller_signal = _motivated_seller_signal(company)
        if seller_signal:
            signals.append(seller_signal)

        bilateral_signal = _bilateral_probability_signal(company)
        if bilateral_signal:
            signals.append(bilateral_signal)

        timing_signal = _market_timing_signal(company, comp_multiple)
        if timing_signal:
            signals.append(timing_signal)

        if not signals:
            alpha = 0.0
        else:
            weights = {
                "multiple_discount": 0.30,
                "operational_improvement": 0.25,
                "motivated_seller": 0.15,
                "bilateral_probability": 0.15,
                "market_timing": 0.15,
            }
            weighted_sum = sum(
                s.strength * weights.get(s.signal_type, 0.10) for s in signals
            )
            max_possible = sum(weights.values())
            alpha = weighted_sum / max_possible

        efficiently_priced = alpha < self._efficiently_priced_threshold

        signal_descriptions = [s.description for s in signals]
        if signals:
            reason = "; ".join(signal_descriptions)
        else:
            reason = "No significant alpha signals detected — likely efficiently priced."

        now = datetime.now(timezone.utc).isoformat()
        result = AlphaScore(
            entity_id=company.entity_id,
            company_name=company.name,
            alpha_score=round(alpha, 4),
            mispricing_reason=reason,
            efficiently_priced=efficiently_priced,
            alpha_signals=signals,
            scored_at=now,
        )

        self._audit.log(AuditEntry(
            action=AuditAction.SCORE_GENERATED,
            actor="alpha_detection",
            entity_id=company.entity_id,
            model_version="v1",
            details={
                "alpha_score": result.alpha_score,
                "efficiently_priced": efficiently_priced,
                "num_signals": len(signals),
            },
            stage="3B",
        ))

        logger.info(
            "alpha_scored",
            entity_id=company.entity_id,
            company=company.name,
            alpha=result.alpha_score,
            efficiently_priced=efficiently_priced,
            signals=len(signals),
        )
        return result

    def score_batch(
        self,
        companies: list[CompanyNormalized],
        valuations: list[ShadowValuation],
        transactions: list[TransactionRecord],
    ) -> list[AlphaScore]:
        """Score a batch of companies and return results sorted by alpha descending."""
        with log_stage(PipelineStage.ALPHA_DETECTION, batch_size=len(companies)):
            results = [
                self.score(company, valuation, transactions)
                for company, valuation in zip(companies, valuations)
            ]
        results.sort(key=lambda r: r.alpha_score, reverse=True)
        return results
