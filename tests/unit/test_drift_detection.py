"""Tests for drift detection."""

import numpy as np
import pytest

from src.feedback.drift_detection import DriftDetector, check_calibration, compute_psi


class TestPSI:
    def test_identical_distributions(self):
        data = np.random.normal(0, 1, 1000)
        psi = compute_psi(data, data)
        assert psi < 0.01

    def test_shifted_distribution(self):
        ref = np.random.normal(0, 1, 1000)
        cur = np.random.normal(1, 1, 1000)  # Shifted mean
        psi = compute_psi(ref, cur)
        assert psi > 0.1  # Should detect drift

    def test_same_distribution_different_samples(self):
        np.random.seed(42)
        ref = np.random.normal(0, 1, 1000)
        cur = np.random.normal(0, 1, 1000)
        psi = compute_psi(ref, cur)
        assert psi < 0.1  # Same distribution, minor sampling noise


class TestCalibration:
    def test_perfect_calibration(self):
        predicted = np.array([0.1, 0.3, 0.5, 0.7, 0.9] * 100)
        actual = np.array([0, 0, 1, 1, 1] * 100)
        ece = check_calibration(predicted, actual)
        assert ece < 0.3  # Roughly calibrated

    def test_uncalibrated(self):
        predicted = np.full(100, 0.9)  # Always predicts 0.9
        actual = np.zeros(100)  # But never happens
        ece = check_calibration(predicted, actual)
        assert ece > 0.5


class TestDriftDetector:
    def test_no_drift_detected(self):
        detector = DriftDetector(psi_threshold=0.1)
        ref = np.random.normal(0.5, 0.1, 1000)
        detector.set_reference("test_model", ref)

        cur = np.random.normal(0.5, 0.1, 1000)
        metric = detector.check_drift("test_model", "v1", cur)
        assert not metric.breached

    def test_drift_detected(self):
        detector = DriftDetector(psi_threshold=0.1)
        ref = np.random.normal(0.5, 0.1, 1000)
        detector.set_reference("test_model", ref)

        cur = np.random.normal(0.8, 0.1, 1000)  # Shifted
        metric = detector.check_drift("test_model", "v1", cur)
        assert metric.breached
        assert metric.metric_value > 0.1
