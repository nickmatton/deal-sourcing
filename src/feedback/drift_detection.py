"""Model drift detection using Population Stability Index (PSI) and feature monitoring."""

import numpy as np
import structlog

from src.common.schemas.feedback import ModelPerformanceMetric

logger = structlog.get_logger("feedback.drift")


def compute_psi(
    reference: np.ndarray,
    current: np.ndarray,
    bins: int = 10,
) -> float:
    """Population Stability Index between reference and current score distributions.

    PSI > 0.1 indicates significant drift; PSI > 0.2 indicates major shift.
    """
    ref_min = min(reference.min(), current.min())
    ref_max = max(reference.max(), current.max())
    bin_edges = np.linspace(ref_min, ref_max, bins + 1)

    ref_counts = np.histogram(reference, bins=bin_edges)[0]
    cur_counts = np.histogram(current, bins=bin_edges)[0]

    # Add small epsilon to avoid division by zero
    eps = 1e-6
    ref_pct = (ref_counts + eps) / (len(reference) + eps * bins)
    cur_pct = (cur_counts + eps) / (len(current) + eps * bins)

    psi = np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct))
    return float(psi)


def check_calibration(
    predicted_probs: np.ndarray,
    actual_outcomes: np.ndarray,
    bins: int = 10,
) -> float:
    """Expected Calibration Error — measures predicted vs observed frequency alignment."""
    bin_edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    total = len(predicted_probs)

    for i in range(bins):
        mask = (predicted_probs >= bin_edges[i]) & (predicted_probs < bin_edges[i + 1])
        if mask.sum() == 0:
            continue
        avg_pred = predicted_probs[mask].mean()
        avg_actual = actual_outcomes[mask].mean()
        ece += (mask.sum() / total) * abs(avg_pred - avg_actual)

    return float(ece)


class DriftDetector:
    """Monitors model score distributions and feature drift."""

    def __init__(self, psi_threshold: float = 0.1) -> None:
        self._psi_threshold = psi_threshold
        self._reference_distributions: dict[str, np.ndarray] = {}

    def set_reference(self, model_name: str, scores: np.ndarray) -> None:
        self._reference_distributions[model_name] = scores

    def check_drift(
        self,
        model_name: str,
        model_version: str,
        current_scores: np.ndarray,
    ) -> ModelPerformanceMetric:
        """Check if current score distribution has drifted from reference."""
        if model_name not in self._reference_distributions:
            logger.warning("drift_detection.no_reference", model=model_name)
            return ModelPerformanceMetric(
                model_name=model_name,
                model_version=model_version,
                metric_name="psi",
                metric_value=0.0,
                threshold=self._psi_threshold,
                breached=False,
                measured_at="",
            )

        reference = self._reference_distributions[model_name]
        psi = compute_psi(reference, current_scores)
        breached = psi > self._psi_threshold

        from datetime import datetime, timezone

        metric = ModelPerformanceMetric(
            model_name=model_name,
            model_version=model_version,
            metric_name="psi",
            metric_value=psi,
            threshold=self._psi_threshold,
            breached=breached,
            measured_at=datetime.now(timezone.utc).isoformat(),
        )

        if breached:
            logger.warning("drift_detection.psi_breached", model=model_name, psi=psi)
        else:
            logger.info("drift_detection.ok", model=model_name, psi=psi)

        return metric
