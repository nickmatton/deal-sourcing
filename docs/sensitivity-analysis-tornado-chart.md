# Sensitivity Analysis & Tornado Charts

## Purpose

Sensitivity analysis answers the question: **"Which assumptions matter most to the deal's return?"** In PE deal underwriting, the projected IRR depends on multiple uncertain inputs (entry price, growth rate, exit multiple, etc.). A tornado chart visualizes how much the IRR swings when each input is varied individually, while all other inputs are held at their base-case values.

This directly informs where the deal team should focus diligence effort and where to negotiate hardest.

## How It Works

### One-at-a-Time (OAT) Perturbation

The tornado chart uses a deterministic **one-at-a-time** methodology:

1. **Establish a base case.** Run the IRR calculation with all parameters set to their median/mode values. This produces a single base-case IRR.

2. **For each parameter** (entry multiple, revenue growth, exit multiple, margin improvement, leverage):
   - **Low scenario:** Set this parameter to its low bound, hold everything else at base case, compute IRR.
   - **High scenario:** Set this parameter to its high bound, hold everything else at base case, compute IRR.

3. **Compute impact** as the absolute spread: `|IRR_high - IRR_low|`.

4. **Sort by impact** descending. The parameter with the widest spread is the most influential driver of returns.

### The Math

For a simplified LBO, the IRR is derived from MOIC (Multiple on Invested Capital):

```
Entry EV        = Entry EBITDA × Entry Multiple
Equity Invested = Entry EV × Equity %
Exit EBITDA     = Entry EBITDA × (1 + g)^hold × (1 + Δmargin)
Exit EV         = Exit EBITDA × Exit Multiple
Debt at Exit    = Debt × (1 + r)^hold
Equity at Exit  = max(Exit EV - Debt at Exit, 0)
MOIC            = Equity at Exit / Equity Invested
IRR             = MOIC^(1/hold) - 1
```

Where:
- `g` = annual revenue growth rate
- `hold` = holding period in years
- `r` = interest rate on debt
- `Δmargin` = cumulative EBITDA margin improvement

The tornado chart re-evaluates this equation once per parameter, per bound (low/high), producing 2 × N IRR values for N parameters.

### Reading the Chart

```
Parameter          Low IRR ◄────────── Base ──────────► High IRR
─────────────────────────────────────────────────────────────────
Exit Multiple      12.3%   ████████████████████████████  28.7%    ← Most sensitive
Entry Multiple     16.1%   ████████████████████████      24.9%
Revenue Growth     17.2%   ██████████████████            23.8%
Margin Improvement 19.0%   ████████████                  22.0%
Leverage           19.5%   ██████████                    21.5%    ← Least sensitive
```

- **Wider bars** = higher sensitivity. The deal team should pressure-test these assumptions.
- **Asymmetric bars** indicate non-linear sensitivity (e.g., entry multiple has an inverse relationship — lower entry = higher IRR).
- A parameter with a very narrow bar can safely use its base-case assumption.

### Why Not Just Use the Monte Carlo Distribution?

The Monte Carlo simulation (which runs 10,000+ scenarios varying all parameters simultaneously) captures **joint uncertainty** — but it obscures *which* parameter is driving the variance. The tornado chart isolates each parameter's marginal contribution, giving the deal team actionable focus areas.

They are complementary:
- **Monte Carlo** → "What is the range of possible outcomes?"
- **Tornado chart** → "Which lever matters most?"

## Implementation

See `src/underwriting/monte_carlo.py`, method `MonteCarloSimulator._compute_sensitivities()`. The implementation uses `_run_deterministic_scenario()` to evaluate the IRR equation with overrides for each parameter at its low and high bound.

Parameters analyzed:
| Parameter | Low Bound | High Bound |
|-----------|-----------|------------|
| Entry Multiple | `entry_multiple_low` | `entry_multiple_high` |
| Revenue Growth | `mean - 2σ` | `mean + 2σ` |
| Exit Multiple | `exit_multiple_bear` | `exit_multiple_bull` |
| Margin Improvement | `margin_improvement_low` | `margin_improvement_high` |
| Leverage | `debt_equity_low` | `debt_equity_high` |

Results are sorted by impact (descending) so the most sensitive parameter always appears first.
