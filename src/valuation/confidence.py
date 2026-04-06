"""Conformalized Quantile Regression for valuation confidence intervals."""

import numpy as np
import structlog

logger = structlog.get_logger("valuation.confidence")


class ConformalizedQuantileRegressor:
    """Produces valid confidence intervals using MAPIE's CQR.

    Guarantees coverage without distributional assumptions —
    critical for valuation where normality does not hold.
    """

    def __init__(self, alpha: float = 0.20) -> None:
        self._alpha = alpha  # 1 - alpha = coverage (80% CI)
        self._mapie_regressor = None
        self._base_model = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        from mapie.regression import MapieQuantileRegressor
        from sklearn.ensemble import GradientBoostingRegressor

        self._base_model = GradientBoostingRegressor(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            random_state=42,
        )

        self._mapie_regressor = MapieQuantileRegressor(
            self._base_model,
            method="quantile",
            cv="split",
            alpha=self._alpha,
        )
        self._mapie_regressor.fit(X, y)
        logger.info("cqr.fitted", alpha=self._alpha)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Returns (point_estimates, intervals) where intervals is (n, 2)."""
        if self._mapie_regressor is None:
            raise RuntimeError("Model not fitted.")

        y_pred, y_pis = self._mapie_regressor.predict(X)
        # y_pis shape: (n_samples, 2, 1) -> squeeze to (n_samples, 2)
        intervals = y_pis[:, :, 0]
        return y_pred, intervals
