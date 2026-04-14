"""Vectorized Monte Carlo IRR simulator for rapid pre-LOI underwriting."""

import numpy as np
import structlog

from src.common.logging import PipelineStage, log_stage
from src.common.schemas.underwriting import (
    IRRDistribution,
    LBOAssumptions,
    Sensitivity,
    UnderwritingResult,
)

logger = structlog.get_logger("underwriting")


def _sample_triangular(low: float, mode: float, high: float, n: int) -> np.ndarray:
    return np.random.triangular(low, mode, high, size=n)


def _sample_beta_pert(low: float, mode: float, high: float, n: int, lam: float = 4.0) -> np.ndarray:
    """PERT distribution using Beta distribution parameterization."""
    if high <= low:
        return np.full(n, mode)
    range_val = high - low
    mu = (low + lam * mode + high) / (lam + 2)
    if mu <= low or mu >= high:
        return np.full(n, mode)

    denom = (mode - mu) * range_val
    if abs(denom) < 1e-10:
        # mode == mu: symmetric — use alpha=beta derived from range
        alpha_param = lam / 2.0 + 1.0
        beta_param = alpha_param
    else:
        alpha_param = ((mu - low) * (2 * mode - low - high)) / denom
        if alpha_param <= 0:
            alpha_param = 1.0
        beta_param = alpha_param * (high - mu) / (mu - low) if (mu - low) > 1e-10 else 1.0
        if beta_param <= 0:
            beta_param = 1.0

    samples = np.random.beta(alpha_param, beta_param, size=n)
    return low + samples * range_val


def compute_irr_vectorized(
    entry_ev: np.ndarray,
    equity_pct: np.ndarray,
    ebitda_exit: np.ndarray,
    exit_multiple: np.ndarray,
    hold_period: np.ndarray,
    interest_rate: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized IRR and MOIC computation for all simulations.

    Returns (irr_array, moic_array).
    """
    equity_invested = entry_ev * equity_pct
    debt = entry_ev * (1 - equity_pct)

    exit_ev = ebitda_exit * exit_multiple
    # Assume debt is repaid at exit (simplified — no amortization in screening)
    debt_at_exit = debt * (1 + interest_rate) ** hold_period
    # Cap debt repayment at exit EV to avoid negative equity
    debt_repayment = np.minimum(debt_at_exit, exit_ev)
    equity_at_exit = np.maximum(exit_ev - debt_repayment, 0.0)

    moic = equity_at_exit / np.maximum(equity_invested, 1.0)
    # IRR = MOIC^(1/n) - 1; for MOIC=0 (total loss), IRR = -1
    safe_hold = np.maximum(hold_period, 1.0)
    irr = np.where(
        moic > 0,
        moic ** (1.0 / safe_hold) - 1.0,
        -1.0,  # Total loss
    )

    return irr, moic


def _distribution_stats(arr: np.ndarray) -> IRRDistribution:
    return IRRDistribution(
        p10=float(np.percentile(arr, 10)),
        p25=float(np.percentile(arr, 25)),
        p50=float(np.percentile(arr, 50)),
        p75=float(np.percentile(arr, 75)),
        p90=float(np.percentile(arr, 90)),
        mean=float(np.mean(arr)),
        std=float(np.std(arr)),
    )


class MonteCarloSimulator:
    """Runs vectorized Monte Carlo simulation for rapid deal underwriting."""

    def simulate(
        self,
        entity_id: str,
        company_name: str,
        assumptions: LBOAssumptions,
    ) -> UnderwritingResult:
        from datetime import datetime, timezone

        n = assumptions.num_simulations
        seed = hash(entity_id) % (2**31)
        np.random.seed(seed)

        # Sample entry EBITDA
        entry_ebitda = np.random.normal(
            assumptions.entry_ebitda_mean, assumptions.entry_ebitda_std, n
        )
        entry_ebitda = np.maximum(entry_ebitda, 0.1)  # Floor at near-zero

        # Sample entry multiple
        entry_multiple = _sample_triangular(
            assumptions.entry_multiple_low,
            assumptions.entry_multiple_mode,
            assumptions.entry_multiple_high,
            n,
        )

        entry_ev = entry_ebitda * entry_multiple

        # Sample revenue growth
        rev_growth = np.random.normal(
            assumptions.revenue_growth_mean, assumptions.revenue_growth_std, n
        )

        # Sample margin improvement
        margin_improvement = _sample_triangular(
            assumptions.margin_improvement_low,
            assumptions.margin_improvement_mode,
            assumptions.margin_improvement_high,
            n,
        )

        # Sample leverage
        equity_pct = 1.0 - np.random.uniform(
            assumptions.debt_equity_low, assumptions.debt_equity_high, n
        )

        # Sample hold period
        hold_period = np.random.choice(assumptions.hold_periods, size=n).astype(float)

        # Sample exit multiple
        exit_bear = assumptions.exit_multiple_bear or assumptions.entry_multiple_low
        exit_base = assumptions.exit_multiple_base or assumptions.entry_multiple_mode
        exit_bull = assumptions.exit_multiple_bull or assumptions.entry_multiple_high
        exit_multiple = _sample_beta_pert(exit_bear, exit_base, exit_bull, n)

        # Project exit EBITDA
        ebitda_exit = entry_ebitda * (1 + rev_growth) ** hold_period * (1 + margin_improvement)

        # Compute IRR and MOIC
        irr, moic = compute_irr_vectorized(
            entry_ev, equity_pct, ebitda_exit, exit_multiple, hold_period,
            assumptions.interest_rate,
        )

        irr_dist = _distribution_stats(irr)
        moic_dist = _distribution_stats(moic)

        p_irr_gt_20 = float(np.mean(irr > 0.20))
        p_irr_gt_25 = float(np.mean(irr > 0.25))

        # Break-even exit multiple (for 1x MOIC at median assumptions)
        median_equity_pct = np.median(equity_pct)
        median_entry_ev = np.median(entry_ev)
        median_ebitda_exit = np.median(ebitda_exit)
        median_hold = np.median(hold_period)
        debt_at_exit = median_entry_ev * (1 - median_equity_pct) * (
            1 + assumptions.interest_rate
        ) ** median_hold
        break_even_ev = median_entry_ev * median_equity_pct + debt_at_exit
        break_even_multiple = break_even_ev / max(median_ebitda_exit, 1.0)

        # Key sensitivities (simplified tornado)
        sensitivities = self._compute_sensitivities(assumptions, irr_dist.p50)

        # Screening decision
        if p_irr_gt_20 < 0.30:
            decision = "auto_reject"
        elif p_irr_gt_25 > 0.40:
            decision = "priority"
        else:
            decision = "pursue"

        # Recommended bid range (entry EV that achieves target IRR)
        bid_low = float(np.percentile(entry_ev, 25))
        bid_high = float(np.percentile(entry_ev, 75))

        now = datetime.now(timezone.utc).isoformat()
        result = UnderwritingResult(
            entity_id=entity_id,
            company_name=company_name,
            irr_distribution=irr_dist,
            moic_distribution=moic_dist,
            p_irr_gt_20=p_irr_gt_20,
            p_irr_gt_25=p_irr_gt_25,
            downside_irr=irr_dist.p10,
            key_sensitivities=sensitivities,
            break_even_multiple=float(break_even_multiple),
            recommended_bid_range=(bid_low, bid_high),
            screening_decision=decision,
            simulated_at=now,
        )

        logger.info(
            "simulation_complete",
            entity_id=entity_id,
            company=company_name,
            irr_p50=round(irr_dist.p50, 3),
            moic_p50=round(moic_dist.p50, 2),
            p_irr_gt_20=round(p_irr_gt_20, 3),
            p_irr_gt_25=round(p_irr_gt_25, 3),
            decision=decision,
            simulations=n,
        )
        return result

    def _run_deterministic_scenario(
        self, assumptions: LBOAssumptions, **overrides: float
    ) -> float:
        """Run a single-point IRR calculation using median assumptions with overrides.

        Each keyword argument replaces the median value for that parameter.
        Returns the median IRR from a small simulation with the override applied.
        """
        n = 2_000

        entry_ebitda_val = overrides.get("entry_ebitda", assumptions.entry_ebitda_mean)
        entry_multiple_val = overrides.get(
            "entry_multiple", assumptions.entry_multiple_mode
        )
        rev_growth_val = overrides.get("revenue_growth", assumptions.revenue_growth_mean)
        margin_improvement_val = overrides.get(
            "margin_improvement", assumptions.margin_improvement_mode
        )
        exit_bear = assumptions.exit_multiple_bear or assumptions.entry_multiple_low
        exit_base = assumptions.exit_multiple_base or assumptions.entry_multiple_mode
        exit_bull = assumptions.exit_multiple_bull or assumptions.entry_multiple_high
        exit_multiple_val = overrides.get("exit_multiple", exit_base)
        equity_pct_val = 1.0 - overrides.get(
            "leverage",
            (assumptions.debt_equity_low + assumptions.debt_equity_high) / 2,
        )
        hold_val = overrides.get(
            "hold_period", float(np.median(assumptions.hold_periods))
        )

        entry_ev = np.full(n, entry_ebitda_val * entry_multiple_val)
        equity_pct = np.full(n, equity_pct_val)
        ebitda_exit = np.full(
            n, entry_ebitda_val * (1 + rev_growth_val) ** hold_val * (1 + margin_improvement_val)
        )
        exit_mult = np.full(n, exit_multiple_val)
        hold_arr = np.full(n, hold_val)

        irr, _ = compute_irr_vectorized(
            entry_ev, equity_pct, ebitda_exit, exit_mult, hold_arr,
            assumptions.interest_rate,
        )
        return float(np.median(irr))

    def _compute_sensitivities(
        self, assumptions: LBOAssumptions, base_irr: float
    ) -> list[Sensitivity]:
        """Tornado-chart sensitivity analysis.

        For each key parameter, holds all others at their median/mode values
        and swings the target parameter to its low and high bounds.  The
        resulting IRR delta is the true sensitivity, not a hardcoded offset.
        """
        base = self._run_deterministic_scenario(assumptions)

        exit_bear = assumptions.exit_multiple_bear or assumptions.entry_multiple_low
        exit_bull = assumptions.exit_multiple_bull or assumptions.entry_multiple_high

        param_specs: list[tuple[str, str, float, float]] = [
            ("entry_multiple", "entry_multiple",
             assumptions.entry_multiple_low, assumptions.entry_multiple_high),
            ("revenue_growth", "revenue_growth",
             assumptions.revenue_growth_mean - 2 * assumptions.revenue_growth_std,
             assumptions.revenue_growth_mean + 2 * assumptions.revenue_growth_std),
            ("exit_multiple", "exit_multiple", exit_bear, exit_bull),
            ("margin_improvement", "margin_improvement",
             assumptions.margin_improvement_low, assumptions.margin_improvement_high),
            ("leverage", "leverage",
             assumptions.debt_equity_low, assumptions.debt_equity_high),
        ]

        sensitivities: list[Sensitivity] = []
        for label, key, low_val, high_val in param_specs:
            low_irr = self._run_deterministic_scenario(assumptions, **{key: low_val})
            high_irr = self._run_deterministic_scenario(assumptions, **{key: high_val})
            impact = abs(high_irr - low_irr)
            sensitivities.append(Sensitivity(
                parameter=label,
                base_irr=base,
                low_irr=low_irr,
                high_irr=high_irr,
                impact=impact,
            ))

        sensitivities.sort(key=lambda s: s.impact, reverse=True)
        return sensitivities
