"""Batch and event-triggered scoring for deal signal detection."""

from datetime import datetime, timezone

import numpy as np
import structlog

from src.common.audit import AuditAction, AuditEntry, AuditLogger
from src.common.logging import PipelineStage, log_stage
from src.common.schemas.signals import DealSignal, TriggerReason
from src.signal_detection.features import SignalFeatures
from src.signal_detection.model import SellProbabilityModel

logger = structlog.get_logger("signal_detection.scoring")

SIGNAL_DESCRIPTIONS = {
    "founder_age": "Founder/owner age suggests succession timing",
    "owner_tenure_years": "Long owner tenure increases transaction likelihood",
    "pe_hold_duration_years": "PE hold duration past typical exit window",
    "employee_growth_30d": "Recent hiring velocity change detected",
    "employee_growth_90d": "90-day hiring trend shift",
    "revenue_growth_yoy": "Revenue growth trajectory inflection",
    "advisor_engagement_detected": "Advisor/banker engagement signals detected",
    "corp_dev_hire_detected": "Corporate development role hiring detected",
    "leadership_change_6m": "Recent leadership transition",
    "external_ceo_appointed": "External CEO appointment (often precedes strategic change)",
    "debt_maturity_months": "Debt maturity creating capital structure pressure",
    "sector_ma_activity_12m": "Elevated M&A activity in sector",
    "website_redesign_detected": "Website/branding refresh (common pre-sale signal)",
}


class DealSignalScorer:
    """Orchestrates sell probability scoring across the company universe."""

    def __init__(
        self,
        model: SellProbabilityModel,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._model = model
        self._audit = audit_logger or AuditLogger()

    def score_batch(
        self,
        entity_ids: list[str],
        company_names: list[str],
        features: list[SignalFeatures],
        revenue_ranges: list[tuple[float, float] | None] | None = None,
        ebitda_ranges: list[tuple[float, float] | None] | None = None,
    ) -> list[DealSignal]:
        """Score a batch of companies for sell probability."""
        with log_stage(PipelineStage.SIGNAL_DETECTION, batch_size=len(entity_ids)) as slog:
            X = np.array([f.to_array() for f in features])
            probabilities = self._model.predict(X)
            explanations = self._model.explain(X, top_k=3)

            now = datetime.now(timezone.utc).isoformat()
            results = []

            for i, (eid, name, prob, top_features) in enumerate(
                zip(entity_ids, company_names, probabilities, explanations)
            ):
                trigger_reasons = [
                    TriggerReason(
                        signal=feat_name,
                        description=SIGNAL_DESCRIPTIONS.get(feat_name, feat_name),
                        confidence=abs(float(shap_val)),
                        source="feature_store",
                    )
                    for feat_name, shap_val in top_features
                ]

                rev_range = revenue_ranges[i] if revenue_ranges else None
                ebitda_range = ebitda_ranges[i] if ebitda_ranges else None

                signal = DealSignal(
                    entity_id=eid,
                    company_name=name,
                    sell_probability=float(prob),
                    trigger_reasons=trigger_reasons,
                    estimated_revenue_range=rev_range,
                    estimated_ebitda_range=ebitda_range,
                    signal_freshness=now,
                    scored_at=now,
                )
                results.append(signal)

                self._audit.log(AuditEntry(
                    action=AuditAction.SCORE_GENERATED,
                    actor="signal_detection",
                    entity_id=eid,
                    model_version="v1",
                    details={"sell_probability": float(prob)},
                    stage="1",
                ))

            high_signal = sum(1 for r in results if r.sell_probability > 0.5)
            slog.info(
                "scored",
                total=len(results),
                high_signal=high_signal,
                mean_probability=round(float(np.mean(probabilities)), 3),
            )
            return results
