"""Tests for Monte Carlo IRR simulator."""

import numpy as np
import pytest

from src.common.schemas.underwriting import LBOAssumptions
from src.underwriting.monte_carlo import (
    MonteCarloSimulator,
    compute_irr_vectorized,
)


class TestIRRComputation:
    def test_basic_irr(self):
        entry_ev = np.array([100.0])
        equity_pct = np.array([0.40])
        ebitda_exit = np.array([20.0])
        exit_multiple = np.array([10.0])
        hold_period = np.array([5.0])

        irr, moic = compute_irr_vectorized(
            entry_ev, equity_pct, ebitda_exit, exit_multiple, hold_period, 0.08
        )
        assert irr.shape == (1,)
        assert moic.shape == (1,)
        assert moic[0] > 1.0  # Should be profitable

    def test_vectorized_output_shape(self):
        n = 100
        irr, moic = compute_irr_vectorized(
            np.full(n, 100.0),
            np.full(n, 0.40),
            np.full(n, 20.0),
            np.full(n, 10.0),
            np.full(n, 5.0),
            0.08,
        )
        assert irr.shape == (n,)
        assert moic.shape == (n,)


class TestMonteCarloSimulator:
    def test_simulation_runs(self):
        simulator = MonteCarloSimulator()
        assumptions = LBOAssumptions(
            entry_ebitda_mean=5_000_000,
            entry_ebitda_std=500_000,
            entry_multiple_low=6.0,
            entry_multiple_mode=8.0,
            entry_multiple_high=10.0,
            revenue_growth_mean=0.10,
            revenue_growth_std=0.05,
            num_simulations=1_000,  # Fewer for test speed
        )

        result = simulator.simulate("test-entity", "Test Company", assumptions)

        assert result.entity_id == "test-entity"
        assert result.irr_distribution.p10 < result.irr_distribution.p90
        assert result.moic_distribution.p10 < result.moic_distribution.p90
        assert 0 <= result.p_irr_gt_20 <= 1
        assert 0 <= result.p_irr_gt_25 <= 1
        assert result.screening_decision in ("auto_reject", "pursue", "priority")
        assert len(result.key_sensitivities) > 0

    def test_high_return_deal_is_priority(self):
        simulator = MonteCarloSimulator()
        assumptions = LBOAssumptions(
            entry_ebitda_mean=10_000_000,
            entry_ebitda_std=500_000,
            entry_multiple_low=4.0,
            entry_multiple_mode=5.0,
            entry_multiple_high=6.0,
            revenue_growth_mean=0.20,
            revenue_growth_std=0.03,
            exit_multiple_bear=8.0,
            exit_multiple_base=10.0,
            exit_multiple_bull=12.0,
            num_simulations=1_000,
        )

        result = simulator.simulate("high-return", "Great Company", assumptions)
        # Low entry multiple + high growth + high exit = should be priority
        assert result.p_irr_gt_20 > 0.3

    def test_bad_deal_rejected(self):
        simulator = MonteCarloSimulator()
        assumptions = LBOAssumptions(
            entry_ebitda_mean=2_000_000,
            entry_ebitda_std=200_000,
            entry_multiple_low=14.0,
            entry_multiple_mode=18.0,
            entry_multiple_high=22.0,
            revenue_growth_mean=-0.05,
            revenue_growth_std=0.02,
            margin_improvement_low=-0.02,
            margin_improvement_mode=0.0,
            margin_improvement_high=0.01,
            exit_multiple_bear=5.0,
            exit_multiple_base=6.0,
            exit_multiple_bull=7.0,
            debt_equity_low=0.60,
            debt_equity_high=0.70,
            num_simulations=1_000,
        )

        result = simulator.simulate("bad-deal", "Overpriced Corp", assumptions)
        # Very high entry multiple + negative growth + much lower exit = should reject
        assert result.screening_decision == "auto_reject"
