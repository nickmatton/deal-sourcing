"""EBITDA margin estimation — sector baselines + LightGBM adjustments."""

from pathlib import Path

import numpy as np
import structlog

from src.common.logging import log_model_event

logger = structlog.get_logger("valuation.margin")

# Damodaran sector median EBITDA margins (approximate, updated annually)
SECTOR_MEDIAN_MARGINS: dict[str, float] = {
    "software": 0.25,
    "saas": 0.22,
    "healthcare_it": 0.18,
    "healthcare_services": 0.15,
    "business_services": 0.15,
    "financial_services": 0.28,
    "industrials": 0.14,
    "manufacturing": 0.12,
    "consumer": 0.10,
    "technology_services": 0.18,
    "distribution": 0.08,
    "construction": 0.10,
    "education": 0.15,
    "default": 0.15,
}

FEATURE_NAMES = [
    "sector_median_margin",
    "employee_growth_90d",
    "revenue_growth_yoy",
    "customer_concentration_proxy",
    "price_increase_detected",
    "is_recurring_revenue",
    "company_age_years",
    "employee_count",
]


class MarginEstimator:
    """Estimates EBITDA margins using sector baselines + LightGBM adjustments."""

    def __init__(self) -> None:
        self._model = None

    @staticmethod
    def get_sector_baseline(sector: str) -> float:
        sector_key = sector.lower().replace(" ", "_").replace("-", "_")
        return SECTOR_MEDIAN_MARGINS.get(
            sector_key, SECTOR_MEDIAN_MARGINS["default"]
        )

    def train(self, X: np.ndarray, y_margins: np.ndarray) -> dict:
        import lightgbm as lgb
        from sklearn.metrics import mean_absolute_error

        dtrain = lgb.Dataset(X, label=y_margins, feature_name=FEATURE_NAMES)

        params = {
            "objective": "regression",
            "metric": "mae",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "seed": 42,
        }

        self._model = lgb.train(
            params,
            dtrain,
            num_boost_round=300,
            valid_sets=[dtrain],
            callbacks=[lgb.log_evaluation(100)],
        )

        preds = self._model.predict(X)
        mae = mean_absolute_error(y_margins, preds)
        log_model_event("train_complete", "margin_estimator", mae=round(mae, 4))
        return {"train_mae": mae}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model not trained.")
        preds = self._model.predict(X)
        return np.clip(preds, 0.0, 0.6)  # Bound to realistic range

    def save(self, path: Path) -> None:
        if self._model is None:
            raise RuntimeError("No model to save.")
        path.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path / "margin_model.txt"))

    def load(self, path: Path) -> None:
        import lightgbm as lgb

        self._model = lgb.Booster(model_file=str(path / "margin_model.txt"))
