"""Revenue estimation model for private companies."""

from pathlib import Path

import numpy as np
import structlog

from src.common.logging import log_model_event

logger = structlog.get_logger("valuation.revenue")

FEATURE_NAMES = [
    "employee_count",
    "web_traffic_monthly",
    "web_traffic_growth_90d",
    "app_downloads_monthly",
    "review_count",
    "review_growth_90d",
    "founded_years_ago",
    "funding_total_usd",
    "sector_revenue_per_employee",
    "is_saas",
    "is_services",
    "is_manufacturing",
]


class RevenueEstimator:
    """XGBoost regressor on log-transformed annual revenue.

    Key insight: employee count is the strongest single proxy
    (~$130K/employee for SaaS, varies by sector).
    """

    def __init__(self) -> None:
        self._model = None

    def train(self, X: np.ndarray, y_revenue: np.ndarray) -> dict:
        """Train on log-transformed revenue. y_revenue in raw USD."""
        import xgboost as xgb
        from sklearn.metrics import mean_absolute_percentage_error

        y_log = np.log1p(y_revenue)
        dtrain = xgb.DMatrix(X, label=y_log, feature_names=FEATURE_NAMES)

        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
            "tree_method": "hist",
            "seed": 42,
        }

        self._model = xgb.train(
            params,
            dtrain,
            num_boost_round=500,
            evals=[(dtrain, "train")],
            early_stopping_rounds=50,
            verbose_eval=100,
        )

        preds_log = self._model.predict(dtrain)
        preds = np.expm1(preds_log)
        mape = mean_absolute_percentage_error(y_revenue, preds)

        log_model_event("train_complete", "revenue_estimator", mape=round(mape, 4))
        return {"train_mape": mape}

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Returns revenue estimates in USD."""
        import xgboost as xgb

        if self._model is None:
            raise RuntimeError("Model not trained.")
        dmat = xgb.DMatrix(X, feature_names=FEATURE_NAMES)
        preds_log = self._model.predict(dmat)
        return np.expm1(preds_log)

    def save(self, path: Path) -> None:
        if self._model is None:
            raise RuntimeError("No model to save.")
        path.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path / "revenue_model.json"))

    def load(self, path: Path) -> None:
        import xgboost as xgb

        self._model = xgb.Booster()
        self._model.load_model(str(path / "revenue_model.json"))
