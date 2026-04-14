"""Shadow Valuation Engine — orchestrates revenue, margin, multiple, and EV estimation."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import structlog

from src.common.audit import AuditAction, AuditEntry, AuditLogger
from src.common.logging import log_step
from src.common.schemas.ingestion import TransactionRecord
from src.common.schemas.valuation import ConfidenceGrade, ShadowValuation, ValueDriver
from src.valuation.confidence import ConformalizedQuantileRegressor

logger = structlog.get_logger("valuation")

SECTOR_DEFAULT_MULTIPLES: dict[str, float] = {
    "software": 12.0,
    "saas": 14.0,
    "healthcare_it": 11.0,
    "healthcare_services": 10.0,
    "business_services": 9.0,
    "financial_services": 10.0,
    "industrials": 7.5,
    "manufacturing": 7.0,
    "consumer": 8.0,
    "technology_services": 10.0,
    "distribution": 7.0,
    "construction": 7.0,
    "education": 9.0,
    "default": 8.0,
}


def _normalize_sector(sector: str | None) -> str:
    if not sector:
        return "default"
    return sector.lower().replace(" ", "_").replace("-", "_")


def derive_multiple_from_comps(
    transactions: list[TransactionRecord],
    target_sector: str | None,
    target_revenue: float | None = None,
) -> tuple[float | None, list[TransactionRecord]]:
    """Select comparable transactions and derive a median EV/EBITDA multiple.

    Matching priority:
      1. Same sector with valid EV/EBITDA multiples
      2. If revenue is known, weight closer-sized deals via IQR trimming
      3. Fall back to all transactions with valid multiples

    Returns (median_multiple, list_of_comps_used).
    """
    sector_key = _normalize_sector(target_sector)

    sector_comps = [
        tx for tx in transactions
        if tx.ev_ebitda_multiple is not None
        and tx.ev_ebitda_multiple > 0
        and _normalize_sector(tx.sector) == sector_key
    ]

    if len(sector_comps) >= 3:
        comps = sector_comps
    else:
        comps = [
            tx for tx in transactions
            if tx.ev_ebitda_multiple is not None and tx.ev_ebitda_multiple > 0
        ]

    if not comps:
        return None, []

    if target_revenue and len(comps) >= 5:
        revenues = [tx.target_revenue for tx in comps if tx.target_revenue]
        if revenues:
            med_rev = np.median(revenues)
            size_filtered = [
                tx for tx in comps
                if tx.target_revenue is not None
                and 0.25 * med_rev <= tx.target_revenue <= 4.0 * med_rev
            ]
            if len(size_filtered) >= 3:
                comps = size_filtered

    multiples = [tx.ev_ebitda_multiple for tx in comps]
    return float(np.median(multiples)), comps


class ShadowValuationEngine:
    """Produces enterprise value estimates for private companies.

    Pipeline: Revenue → Margin → Multiple → EV with confidence intervals.
    When comparable transactions are provided, the engine derives the entry
    multiple from real deal data instead of using a hardcoded default.
    """

    def __init__(
        self,
        revenue_estimator=None,
        margin_estimator=None,
        multiple_predictor=None,
        multiple_cqr: ConformalizedQuantileRegressor | None = None,
        illiquidity_discount: float = 0.20,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._revenue_est = revenue_estimator
        self._margin_est = margin_estimator
        self._multiple_pred = multiple_predictor
        self._multiple_cqr = multiple_cqr
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
        comparable_transactions: list[TransactionRecord] | None = None,
        company_sector: str | None = None,
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
        #
        # Blended approach — comps and ML answer different questions:
        #   Comps:    "What does the market pay for this sector/size?"
        #   ML model: "How should this company's specific characteristics
        #              shift the multiple up or down from the market baseline?"
        #
        # When both are available, blend them.  When only one is available,
        # use it.  Sector defaults are the last resort.
        comp_set: list[TransactionRecord] = []
        with log_step("multiple_prediction", company_log) as mulog:
            comp_multiple: float | None = None
            ml_multiple: float | None = None

            # --- Comps (always attempted when transactions are provided) ---
            if comparable_transactions:
                comp_multiple, comp_set = derive_multiple_from_comps(
                    comparable_transactions, company_sector, known_revenue,
                )
                if comp_multiple is not None:
                    mulog.debug(
                        "comp_derived",
                        multiple=round(comp_multiple, 1),
                        num_comps=len(comp_set),
                    )

            # --- ML model (always attempted when a trained model exists) ---
            if self._multiple_pred is not None:
                ml_multiple = float(
                    self._multiple_pred.predict(multiple_features.reshape(1, -1))[0]
                )
                mulog.debug("ml_predicted", multiple=round(ml_multiple, 1))

            # --- Blend or fall back ---
            if comp_multiple is not None and ml_multiple is not None:
                # Weight comps more heavily when the comp set is large and
                # tightly clustered; weight the ML model more when comps are
                # sparse or dispersed.
                num_comps = len(comp_set)
                if num_comps >= 5:
                    comp_weight = 0.60
                elif num_comps >= 3:
                    comp_weight = 0.50
                else:
                    comp_weight = 0.35
                ml_weight = 1.0 - comp_weight
                predicted_multiple = comp_weight * comp_multiple + ml_weight * ml_multiple
                mulog.debug(
                    "blended",
                    multiple=round(predicted_multiple, 1),
                    comp_weight=comp_weight,
                    ml_weight=ml_weight,
                    comp_multiple=round(comp_multiple, 1),
                    ml_multiple=round(ml_multiple, 1),
                )
            elif comp_multiple is not None:
                predicted_multiple = comp_multiple
                mulog.debug("comp_only", multiple=round(predicted_multiple, 1))
            elif ml_multiple is not None:
                predicted_multiple = ml_multiple
                mulog.debug("ml_only", multiple=round(predicted_multiple, 1))
            else:
                sector_key = _normalize_sector(company_sector)
                predicted_multiple = SECTOR_DEFAULT_MULTIPLES.get(
                    sector_key, SECTOR_DEFAULT_MULTIPLES["default"]
                )
                mulog.debug("sector_default", multiple=predicted_multiple)

        # Step 4: Enterprise value with illiquidity discount
        ev_pre_discount = estimated_ebitda * predicted_multiple
        ev_point = ev_pre_discount * (1 - self._illiquidity_discount)

        # Confidence interval — prefer CQR when a fitted model is available,
        # otherwise fall back to the heuristic approach.
        has_comps = len(comp_set) >= 3
        data_points = sum([
            known_revenue is not None,
            known_ebitda is not None,
            self._revenue_est is not None,
            self._margin_est is not None,
            self._multiple_pred is not None,
            has_comps,
        ])
        if data_points >= 4:
            grade = ConfidenceGrade.A
        elif data_points >= 2:
            grade = ConfidenceGrade.B
        else:
            grade = ConfidenceGrade.C

        cqr_used = False
        if self._multiple_cqr is not None and self._multiple_cqr._mapie_regressor is not None:
            try:
                _, intervals = self._multiple_cqr.predict(multiple_features.reshape(1, -1))
                multiple_low = float(max(intervals[0, 0], 1.0))
                multiple_high = float(intervals[0, 1])
                ev_low = estimated_ebitda * multiple_low * (1 - self._illiquidity_discount)
                ev_high = estimated_ebitda * multiple_high * (1 - self._illiquidity_discount)
                cqr_used = True
                company_log.debug(
                    "cqr_ci",
                    multiple_low=round(multiple_low, 1),
                    multiple_high=round(multiple_high, 1),
                )
            except Exception:
                cqr_used = False

        if not cqr_used:
            ci_width = {ConfidenceGrade.A: 0.20, ConfidenceGrade.B: 0.30, ConfidenceGrade.C: 0.40}[grade]

            if has_comps and len(comp_set) >= 3:
                comp_multiples = [tx.ev_ebitda_multiple for tx in comp_set]
                iqr = float(np.percentile(comp_multiples, 75) - np.percentile(comp_multiples, 25))
                comp_ci_width = iqr / max(predicted_multiple, 1.0)
                ci_width = min(ci_width, max(comp_ci_width, 0.10))

            ev_low = ev_point * (1 - ci_width)
            ev_high = ev_point * (1 + ci_width)

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
