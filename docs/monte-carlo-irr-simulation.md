# Monte Carlo IRR Simulation

## Purpose

The Monte Carlo simulator answers: **"Given uncertainty in every assumption, what is the distribution of possible returns for this deal?"** Instead of producing a single IRR number (which implies false precision), it generates 10,000+ scenarios by sampling each uncertain input from a probability distribution, producing a full return distribution.

This lets the deal team reason about downside risk ("What's the probability we lose money?") and upside potential ("What's the probability of a 3x+ return?").

## Model Structure

### Input Assumptions

Each uncertain parameter is sampled from a specified distribution:

| Parameter | Distribution | Rationale |
|-----------|-------------|-----------|
| Entry EBITDA | Normal(μ, σ) | Centered on estimated EBITDA with uncertainty band |
| Entry Multiple | Triangular(low, mode, high) | Bounded range with a most-likely value |
| Revenue Growth | Normal(μ, σ) | Symmetric uncertainty around expected growth |
| Margin Improvement | Triangular(low, mode, high) | Bounded operational upside |
| Exit Multiple | PERT(bear, base, bull) | Fat-tailed distribution reflecting exit uncertainty |
| Leverage (Debt/EV) | Uniform(low, high) | Range of feasible capital structures |
| Hold Period | Discrete choice from {3, 4, 5, 6, 7} years | Reflects typical PE hold horizons |

### The PERT Distribution

The exit multiple uses a **PERT distribution** (a reparameterized Beta distribution) instead of Triangular because it produces more realistic tails. In a Triangular distribution, probability drops linearly to zero at the bounds. In PERT, the distribution can have heavier tails, better reflecting the reality that exit multiples occasionally land far from the mode.

```
μ = (low + λ × mode + high) / (λ + 2)     where λ = 4
α = (μ - low)(2×mode - low - high) / ((mode - μ)(high - low))
β = α × (high - μ) / (μ - low)

X ~ low + Beta(α, β) × (high - low)
```

### IRR Computation (Vectorized)

All 10,000 scenarios are computed simultaneously using NumPy vectorization:

```
1.  Entry EV        = Entry EBITDA × Entry Multiple
2.  Equity Invested = Entry EV × (1 - Debt/EV)
3.  Debt            = Entry EV × (Debt/EV)
4.  Exit EBITDA     = Entry EBITDA × (1 + g)^hold × (1 + Δmargin)
5.  Exit EV         = Exit EBITDA × Exit Multiple
6.  Debt at Exit    = Debt × (1 + r)^hold          [no amortization in screening]
7.  Debt Repayment  = min(Debt at Exit, Exit EV)    [can't repay more than EV]
8.  Equity at Exit  = max(Exit EV - Debt Repayment, 0)
9.  MOIC            = Equity at Exit / Equity Invested
10. IRR             = MOIC^(1/hold) - 1             [for MOIC > 0; else -100%]
```

**Note:** This is a simplified LBO model for screening purposes. It assumes:
- No interim cash flows (dividends, management fees)
- No debt amortization (bullet repayment at exit)
- No working capital adjustments
- No add-on acquisitions

These simplifications are acceptable for screening (Stage 5) because the goal is to rank opportunities, not produce a bankable model.

### Why MOIC-Based IRR?

The standard IRR formula requires solving for the discount rate in `NPV = 0`, which involves root-finding (Newton-Raphson). For 10,000 simulations, this is slow. The MOIC-based approximation:

```
IRR ≈ MOIC^(1/hold) - 1
```

...is exact when there is a single cash outflow at t=0 and a single cash inflow at t=hold. Since the screening model has no interim cash flows, this is an exact solution, not an approximation.

## Output

### Distribution Statistics

| Metric | Meaning |
|--------|---------|
| P10 | 10th percentile — downside case |
| P25 | 25th percentile |
| P50 | Median — base case |
| P75 | 75th percentile |
| P90 | 90th percentile — upside case |
| Mean | Average across all simulations |
| Std | Standard deviation of returns |

### Key Risk Metrics

| Metric | Calculation | Purpose |
|--------|-------------|---------|
| P(IRR > 20%) | Fraction of simulations exceeding 20% IRR | Likelihood of meeting fund hurdle |
| P(IRR > 25%) | Fraction of simulations exceeding 25% IRR | Likelihood of top-quartile return |
| Break-even Multiple | Exit multiple needed for 1.0x MOIC at median assumptions | How much room before capital loss |

### Screening Decision

| Condition | Decision |
|-----------|----------|
| P(IRR > 20%) < 30% | **Auto Reject** — insufficient probability of meeting hurdle |
| P(IRR > 25%) > 40% | **Priority** — strong likelihood of top-quartile return |
| Otherwise | **Pursue** — merits further diligence |

## Reproducibility

Each simulation is seeded with a hash of the entity ID (`hash(entity_id) % 2^31`), ensuring:
- The same company always produces the same simulation results (deterministic for auditing)
- Different companies get different random draws (unlike a global fixed seed)

## Implementation

See `src/underwriting/monte_carlo.py`. The `MonteCarloSimulator.simulate()` method runs the full simulation and returns an `UnderwritingResult` with IRR/MOIC distributions, risk metrics, bid range, and screening decision.
