"""EV/EBITDA multiple prediction model."""

from pathlib import Path

import numpy as np
import structlog

from src.common.logging import log_model_event

logger = structlog.get_logger("valuation.multiple")

FEATURE_NAMES = [
    "sector_median_multiple",
    "estimated_revenue_growth",
    "estimated_ebitda_margin",
    "recurring_revenue_pct",
    "company_age_years",
    "employee_count",
    "deal_size_bucket",  # 0=small, 1=mid, 2=large
    "public_comp_multiple",
    "credit_spread",
    "ipo_ma_volume_index",
]


class MultiplePredictor:
    """Predicts appropriate EV/EBITDA multiple for a target company.

    Accounts for sector, growth, profitability, size discount, and market conditions.
    """

    def __init__(self) -> None:
        self._model = None

    def train(self, X: np.ndarray, y_multiples: np.ndarray) -> dict:
        import lightgbm as lgb
        from sklearn.metrics import mean_absolute_error

        dtrain = lgb.Dataset(X, label=y_multiples, feature_name=FEATURE_NAMES)

        params = {
            "objective": "regression",
            "metric": "mae",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 10,
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
        mae = mean_absolute_error(y_multiples, preds)
        log_model_event("train_complete", "multiple_predictor", mae=round(mae, 4))
        return {"train_mae": mae}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model not trained.")
        preds = self._model.predict(X)
        return np.clip(preds, 2.0, 30.0)  # Bound to realistic multiples

    def save(self, path: Path) -> None:
        if self._model is None:
            raise RuntimeError("No model to save.")
        path.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path / "multiple_model.txt"))

    def load(self, path: Path) -> None:
        import lightgbm as lgb

        self._model = lgb.Booster(model_file=str(path / "multiple_model.txt"))
