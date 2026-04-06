"""Shadow Valuation Engine — orchestrates revenue, margin, multiple, and EV estimation."""

from datetime import datetime, timezone

import numpy as np
import structlog

from src.common.audit import AuditAction, AuditEntry, AuditLogger
from src.common.logging import log_step
from src.common.schemas.valuation import ConfidenceGrade, ShadowValuation, ValueDriver

logger = structlog.get_logger("valuation")


class ShadowValuationEngine:
    """Produces enterprise value estimates for private companies.

    Pipeline: Revenue → Margin → Multiple → EV with confidence intervals.
    """

    def __init__(
        self,
        revenue_estimator=None,
        margin_estimator=None,
        multiple_predictor=None,
        illiquidity_discount: float = 0.20,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._revenue_est = revenue_estimator
        self._margin_est = margin_estimator
        self._multiple_pred = multiple_predictor
        self._illiquidity_discount = illiquidity_discount
        self._audit = audit_logger or AuditLogger()

    def value_company(
        self,
        entity_id: str,
        company_name: str,
        revenue_features: np.ndarray,
        margin_features: np.ndarray,
        multiple_features: np.ndarray,
        known_revenue: float | None = None,
        known_ebitda: float | None = None,
    ) -> ShadowValuation:
        """Produce a shadow valuation for a single company."""
        now = datetime.now(timezone.utc).isoformat()
        company_log = logger.bind(entity_id=entity_id, company=company_name)

        # Step 1: Revenue estimation (use known if available)
        with log_step("revenue_estimation", company_log) as rlog:
            if known_revenue is not None:
                estimated_revenue = known_revenue
                rlog.debug("using_known_revenue", revenue=known_revenue)
            elif self._revenue_est is not None:
                estimated_revenue = float(
                    self._revenue_est.predict(revenue_features.reshape(1, -1))[0]
                )
                rlog.debug("estimated", revenue=round(estimated_revenue))
            else:
                raise ValueError("No revenue estimator and no known revenue provided")

        # Step 2: Margin estimation
        with log_step("margin_estimation", company_log) as mlog:
            if known_ebitda is not None and estimated_revenue > 0:
                estimated_ebitda = known_ebitda
                margin = known_ebitda / estimated_revenue
                mlog.debug("using_known_ebitda", margin=round(margin, 3))
            elif self._margin_est is not None:
                margin = float(
                    self._margin_est.predict(margin_features.reshape(1, -1))[0]
                )
                estimated_ebitda = estimated_revenue * margin
                mlog.debug("estimated", margin=round(margin, 3), ebitda=round(estimated_ebitda))
            else:
                margin = 0.15
                estimated_ebitda = estimated_revenue * margin
                mlog.debug("default_margin", margin=margin)

        # Step 3: Multiple prediction
        with log_step("multiple_prediction", company_log) as mulog:
            if self._multiple_pred is not None:
                predicted_multiple = float(
                    self._multiple_pred.predict(multiple_features.reshape(1, -1))[0]
                )
                mulog.debug("predicted", multiple=round(predicted_multiple, 1))
            else:
                predicted_multiple = 8.0
                mulog.debug("default_multiple", multiple=predicted_multiple)

        # Step 4: Enterprise value with illiquidity discount
        ev_pre_discount = estimated_ebitda * predicted_multiple
        ev_point = ev_pre_discount * (1 - self._illiquidity_discount)

        # Simple confidence interval (±30% for now; CQR used in production)
        ci_width = 0.30
        ev_low = ev_point * (1 - ci_width)
        ev_high = ev_point * (1 + ci_width)

        # Confidence grade based on data availability
        data_points = sum([
            known_revenue is not None,
            known_ebitda is not None,
            self._revenue_est is not None,
            self._margin_est is not None,
            self._multiple_pred is not None,
        ])
        if data_points >= 4:
            grade = ConfidenceGrade.A
        elif data_points >= 2:
            grade = ConfidenceGrade.B
        else:
            grade = ConfidenceGrade.C

        valuation = ShadowValuation(
            entity_id=entity_id,
            company_name=company_name,
            ev_point_estimate=ev_point,
            ev_range_80ci=(ev_low, ev_high),
            estimated_revenue=estimated_revenue,
            estimated_ebitda=estimated_ebitda,
            implied_ev_ebitda_multiple=predicted_multiple,
            implied_ev_revenue_multiple=(
                ev_point / estimated_revenue if estimated_revenue > 0 else None
            ),
            key_value_drivers=[],  # Populated by SHAP when models are trained
            confidence_grade=grade,
            illiquidity_discount_applied=self._illiquidity_discount,
            data_freshness=now,
            valued_at=now,
        )

        self._audit.log(AuditEntry(
            action=AuditAction.SCORE_GENERATED,
            actor="shadow_valuation",
            entity_id=entity_id,
            model_version="v1",
            details={
                "ev_point_estimate": ev_point,
                "implied_multiple": predicted_multiple,
                "confidence_grade": grade.value,
            },
            stage="3A",
        ))

        company_log.info(
            "valuation_complete",
            ev=f"${ev_point:,.0f}",
            ev_range=f"${ev_low:,.0f}-${ev_high:,.0f}",
            multiple=round(predicted_multiple, 1),
            grade=grade.value,
        )
        return valuation
