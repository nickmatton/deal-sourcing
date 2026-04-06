"""XGBoost-based sell probability model with focal loss and SHAP explanations."""

from pathlib import Path

import numpy as np
import structlog

from src.common.logging import log_model_event, log_step
from src.signal_detection.features import SignalFeatures

logger = structlog.get_logger("signal_detection")


def focal_loss_objective(alpha: float = 0.25, gamma: float = 2.0):
    """Custom focal loss for XGBoost to handle severe class imbalance (<1% positive rate)."""

    def _focal_loss(y_pred: np.ndarray, dtrain) -> tuple[np.ndarray, np.ndarray]:  # type: ignore[no-untyped-def]
        y_true = dtrain.get_label()
        p = 1.0 / (1.0 + np.exp(-y_pred))
        p = np.clip(p, 1e-7, 1 - 1e-7)

        # Gradient
        grad = (
            alpha * y_true * (1 - p) ** gamma * (gamma * p * np.log(p) + p - 1)
            + (1 - alpha) * p**gamma * ((1 - y_true) * (1 - gamma * (1 - p) * np.log(1 - p)) - (1 - p))
        )
        # Hessian (approximation)
        hess = np.abs(grad) * (1 - np.abs(grad))
        hess = np.maximum(hess, 1e-7)
        return grad, hess

    return _focal_loss


class SellProbabilityModel:
    """Deal signal detection model (Stage 1).

    Uses XGBoost with focal loss for class-imbalanced sell probability prediction.
    """

    def __init__(self) -> None:
        self._model = None
        self._calibrator = None
        self._feature_names = SignalFeatures.feature_names()

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        val_X: np.ndarray | None = None,
        val_y: np.ndarray | None = None,
    ) -> dict:
        """Train the sell probability model.

        Returns training metrics dict.
        """
        import xgboost as xgb
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import roc_auc_score

        dtrain = xgb.DMatrix(X, label=y, feature_names=self._feature_names)

        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 5,
            "eval_metric": "auc",
            "tree_method": "hist",
            "seed": 42,
        }

        evals = [(dtrain, "train")]
        if val_X is not None and val_y is not None:
            dval = xgb.DMatrix(val_X, label=val_y, feature_names=self._feature_names)
            evals.append((dval, "val"))

        self._model = xgb.train(
            params,
            dtrain,
            num_boost_round=500,
            evals=evals,
            obj=focal_loss_objective(alpha=0.25, gamma=2.0),
            early_stopping_rounds=50,
            verbose_eval=50,
        )

        # Isotonic calibration for reliable probabilities
        raw_preds = self._model.predict(dtrain)
        from sklearn.isotonic import IsotonicRegression

        self._calibrator = IsotonicRegression(out_of_bounds="clip")
        self._calibrator.fit(raw_preds, y)

        # Metrics
        calibrated = self._calibrator.predict(raw_preds)
        train_auc = roc_auc_score(y, calibrated)
        metrics = {"train_auc": train_auc}

        if val_X is not None and val_y is not None:
            val_preds = self.predict(val_X)
            val_auc = roc_auc_score(val_y, val_preds)
            metrics["val_auc"] = val_auc

        log_model_event(
            "train_complete", "sell_probability",
            train_auc=metrics["train_auc"],
            val_auc=metrics.get("val_auc"),
            num_rounds=self._model.best_iteration if hasattr(self._model, "best_iteration") else None,
        )
        return metrics

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return calibrated sell probabilities."""
        import xgboost as xgb

        if self._model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        dmat = xgb.DMatrix(X, feature_names=self._feature_names)
        raw_preds = self._model.predict(dmat)

        if self._calibrator is not None:
            return self._calibrator.predict(raw_preds)
        return raw_preds

    def explain(self, X: np.ndarray, top_k: int = 3) -> list[list[tuple[str, float]]]:
        """Return top-k SHAP-based explanations for each prediction."""
        import shap

        if self._model is None:
            raise RuntimeError("Model not trained.")

        explainer = shap.TreeExplainer(self._model)
        shap_values = explainer.shap_values(X)

        explanations = []
        for i in range(len(X)):
            feature_impacts = list(zip(self._feature_names, shap_values[i]))
            feature_impacts.sort(key=lambda x: abs(x[1]), reverse=True)
            explanations.append(feature_impacts[:top_k])

        return explanations

    def save(self, path: Path) -> None:
        import joblib

        if self._model is None:
            raise RuntimeError("No model to save.")
        path.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path / "xgb_model.json"))
        if self._calibrator is not None:
            joblib.dump(self._calibrator, path / "calibrator.pkl")
        log_model_event("model_saved", "sell_probability", path=str(path))

    def load(self, path: Path) -> None:
        import joblib
        import xgboost as xgb

        self._model = xgb.Booster()
        self._model.load_model(str(path / "xgb_model.json"))
        calibrator_path = path / "calibrator.pkl"
        if calibrator_path.exists():
            self._calibrator = joblib.load(calibrator_path)
        log_model_event("model_loaded", "sell_probability", path=str(path))
