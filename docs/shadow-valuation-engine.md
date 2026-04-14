# Shadow Valuation Engine

## Purpose

The Shadow Valuation Engine produces **enterprise value (EV) estimates for private companies** that have no publicly available financial statements. It synthesizes revenue estimation, margin estimation, and multiple prediction into a single EV range with a confidence grade.

"Shadow" valuation means this is the buyer's internal estimate — it is not a formal appraisal and is never shared externally. It serves three downstream purposes:

1. **Ranking** — Sort targets by attractiveness (combining fit + value)
2. **Feasibility** — Does this company fit the fund's check size?
3. **Anchoring** — Provide a price reference for outreach calibration and bid strategy

## Pipeline

The engine operates as a four-step pipeline:

```
Revenue → Margin → Multiple → Enterprise Value
   ↓         ↓         ↓            ↓
 Known or  Known or  Trained ML   EV = EBITDA × Multiple × (1 - discount)
 ML model  ML model  or Comps
                     or Sector
                     default
```

### Step 1: Revenue Estimation

**If known revenue is provided** (from research, data vendors, or estimates), it is used directly.

**If not**, a trained `RevenueEstimator` (XGBoost on log-transformed revenue) predicts revenue from proxy features:

| Feature | Rationale |
|---------|-----------|
| Employee count | Strongest single proxy (~$130K/employee for SaaS, varies by sector) |
| Web traffic (monthly) | Correlates with top-of-funnel / customer base |
| Web traffic growth (90d) | Growth trajectory signal |
| App downloads | Relevant for mobile-first businesses |
| Review count / growth | Product adoption signal (G2, Capterra) |
| Founded years ago | Company maturity proxy |
| Total funding | Capital deployed ≈ scale indicator |
| Sector revenue/employee | Sector-specific calibration |
| Sector flags (SaaS, services, manufacturing) | Sector-specific revenue models |

The model trains on `log(1 + revenue)` to handle the right-skewed revenue distribution, then transforms back with `exp(pred) - 1`.

### Step 2: Margin Estimation

**If known EBITDA is provided**, the margin is derived: `margin = EBITDA / Revenue`.

**If not**, a trained `MarginEstimator` (LightGBM) predicts the EBITDA margin. When no trained model is available, the engine falls back to sector-median margins from Damodaran research:

| Sector | Median EBITDA Margin |
|--------|---------------------|
| Software | 25% |
| SaaS | 22% |
| Financial Services | 28% |
| Healthcare IT | 18% |
| Business Services | 15% |
| Industrials | 14% |
| Manufacturing | 12% |
| Consumer | 10% |
| Distribution | 8% |

EBITDA is then: `EBITDA = Revenue × Margin`.

### Step 3: Multiple Prediction

Comps and ML models answer different questions about multiples, so the engine blends them rather than treating them as a priority hierarchy:

- **Comparable transactions** answer: "What does the market actually pay for this sector/size?" This is the empirical anchor grounded in real deal-clearing prices. See [Comparable Transactions](comparable-transactions.md) for methodology.
- **Trained ML model** (`MultiplePredictor`, LightGBM) answers: "How should this specific company's characteristics shift the multiple up or down from the market baseline?" It captures non-linear interactions between growth rate, margin quality, recurring revenue %, size, customer concentration, and market conditions.

When both are available, they are blended with a weighted average. The comp weight increases with comp set size (more/better comps = more reliable market signal):

| Comp Set Size | Comp Weight | ML Weight |
|---------------|-------------|-----------|
| 5+ comps | 60% | 40% |
| 3-4 comps | 50% | 50% |
| 1-2 comps | 35% | 65% |

When only one source is available, it is used directly. Sector defaults (a lookup table of typical multiples) are the fallback when neither comps nor a trained model exist.

### Step 4: Enterprise Value

```
EV_pre_discount = EBITDA × Multiple
EV              = EV_pre_discount × (1 - illiquidity_discount)
```

The **illiquidity discount** (default 20%) reflects the reduced marketability of a private company compared to a publicly traded stock. The buyer cannot exit the position at will; liquidity requires finding another buyer, which takes time and involves transaction costs.

## Confidence Grading

The engine assigns a confidence grade based on data availability:

| Grade | Criteria | CI Width |
|-------|----------|----------|
| A | 4+ data signals (known financials + trained models + comps) | ±20% |
| B | 2-3 data signals | ±30% |
| C | Sparse data (mostly defaults) | ±40% |

When comparable transactions are available (3+ comps), the confidence interval width is refined using the **interquartile range** of comp multiples, which provides a data-driven measure of pricing dispersion rather than an arbitrary fixed band.

```
ci_width = min(default_ci_width, max(IQR / predicted_multiple, 0.10))
```

## Output

The `ShadowValuation` schema includes:

| Field | Description |
|-------|-------------|
| `ev_point_estimate` | Midpoint EV in USD |
| `ev_range_80ci` | 80% confidence interval (low, high) |
| `estimated_revenue` | Revenue used in calculation |
| `estimated_ebitda` | EBITDA used in calculation |
| `implied_ev_ebitda_multiple` | The multiple applied |
| `implied_ev_revenue_multiple` | EV / Revenue for reference |
| `confidence_grade` | A, B, or C |
| `key_value_drivers` | SHAP-derived feature importances (when ML models are used) |
| `illiquidity_discount_applied` | The discount percentage used |

## Implementation

See `src/valuation/engine.py` for the orchestration engine, and individual estimators:
- `src/valuation/revenue_estimator.py` — XGBoost revenue predictor
- `src/valuation/margin_estimator.py` — LightGBM margin predictor with sector baselines
- `src/valuation/multiple_predictor.py` — LightGBM multiple predictor
- `src/valuation/confidence.py` — Conformalized Quantile Regression for production-grade CIs
