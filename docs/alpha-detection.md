# Alpha Detection (Mispricing Scoring)

## Purpose

Alpha detection identifies acquisition targets where the buyer has a **structural advantage** — meaning the buyer can extract more value from the asset than the market price implies. A high alpha score means the deal is likely to generate outsized returns beyond what a competitive auction would produce.

This is the core of the "proprietary deal flow" thesis: not just finding companies, but finding companies where *this specific buyer* has an edge.

## What Is Alpha in Private Equity?

In public markets, alpha is the return above what the market provides. In PE deal sourcing, alpha comes from five sources:

### 1. Multiple Discount (Weight: 30%)

The target's implied entry multiple is below the median multiple from comparable transactions. This can happen when:
- The company is not actively marketed
- The seller is unsophisticated about market pricing
- The company has characteristics that obscure its true quality (e.g., recurring revenue classified as project revenue in databases)

**Calculation:**

```
discount = (comp_median_multiple - implied_entry_multiple) / comp_median_multiple
strength = min(discount / 0.40, 1.0)
```

A 20% discount to comps would produce `strength = 0.50`. Capped at 1.0 for discounts >= 40%.

### 2. Operational Improvement (Weight: 25%)

The target's EBITDA margin is below the sector median, and the buyer has an operating playbook to close the gap. This is value the buyer *creates* through active ownership, not value that exists in the market price.

**Calculation:**

```
gap = sector_median_margin - company_margin
strength = min(gap / sector_median_margin, 1.0)
```

Sector medians are sourced from Damodaran data (see `src/valuation/margin_estimator.py` for the lookup table). A SaaS company running at 15% EBITDA margin vs. a 22% sector median has a 7pp gap, producing `strength = 7/22 ≈ 0.32`.

### 3. Motivated Seller (Weight: 15%)

Certain ownership structures correlate with seller urgency, which reduces auction competition and creates buyer leverage:

- **Founder-owned 20+ years:** Succession pressure, fatigue, estate planning
- **PE-backed:** Fund lifecycle pressure (approaching exit window)
- **Multi-generational family (30+ years):** Generational transition risk

Each signal adds +0.4 to strength (capped at 1.0).

### 4. Bilateral Probability (Weight: 15%)

The likelihood that the deal can be completed without a competitive auction process. Bilateral deals typically close at 10-25% below auction prices because the seller avoids the uncertainty and timeline of a formal process.

**Signals that increase bilateral probability:**
- Owner-operator (founder/family) — less institutional, less likely to hire a banker
- Sub-$50M revenue — below the threshold where investment banks actively seek mandates
- Small workforce (<200) — less institutional infrastructure, decisions made by 1-2 people

### 5. Market Timing (Weight: 15%)

Current sector multiples are depressed relative to long-run historical norms, creating a buying window. If the buyer believes multiples will revert to the mean during the hold period, the entry price implicitly includes a discount.

**Calculation:**

```
discount = (historical_norm_multiple - current_comp_multiple) / historical_norm_multiple
strength = min(discount / 0.30, 1.0)
```

A >10% discount to historical norms triggers this signal.

## Composite Alpha Score

The final alpha score is a weighted average:

```
alpha = Σ(signal_strength × signal_weight) / Σ(all_weights)
```

Where weights are: multiple_discount=0.30, operational_improvement=0.25, motivated_seller=0.15, bilateral_probability=0.15, market_timing=0.15.

**Interpretation:**
| Alpha Score | Label | Meaning |
|-------------|-------|---------|
| < 0.20 | Efficiently Priced | No meaningful buyer edge; likely auction bait |
| 0.20 - 0.40 | Moderate Alpha | Some structural advantage; worth pursuing |
| 0.40 - 0.60 | Strong Alpha | Multiple advantages aligned; high priority |
| > 0.60 | Exceptional Alpha | Rare; multiple reinforcing signals |

Targets flagged as `efficiently_priced = True` (alpha < 0.20) are deprioritized in the outreach queue — pursuing them means competing in an auction without a differentiated angle.

## Implementation

See `src/alpha_detection/scorer.py`. The `AlphaScorer.score()` method evaluates all five signal types against a single company and its shadow valuation, using comparable transactions as the pricing benchmark.
