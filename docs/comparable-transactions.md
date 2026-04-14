# Comparable Transaction Analysis

## Purpose

Comparable transaction analysis (or "transaction comps") is the most widely used valuation method in private equity. It answers: **"What have buyers actually paid for similar companies?"** Rather than building a theoretical model of a company's value, it grounds the valuation in market-clearing prices from real transactions.

## How It Works

### Step 1: Select Comparable Transactions

The engine identifies relevant historical M&A transactions using a matching hierarchy:

1. **Sector match (primary):** Transactions in the same sector as the target, with valid EV/EBITDA multiples.
2. **Size filtering (secondary):** If 5+ sector comps exist and the target's revenue is known, filter to transactions where the target revenue is within 0.25x–4.0x of the comp set's median revenue. This avoids comparing a $10M-revenue company against a $500M transaction.
3. **Fallback:** If fewer than 3 sector comps are found, use all available transactions with valid multiples.

### Step 2: Derive the Multiple

The engine uses the **median** EV/EBITDA multiple from the selected comp set. Median is preferred over mean because M&A multiples are right-skewed (a few very high-multiple deals can distort the average).

```
comp_multiple = median(EV/EBITDA for each comparable transaction)
```

### Step 3: Apply to Target

```
Enterprise Value = Target EBITDA × Comp Multiple × (1 - Illiquidity Discount)
```

The **illiquidity discount** (default 20%) reflects that private companies cannot be sold as readily as public equities. This discount narrows as the company gets larger, more institutional, and more likely to attract multiple bidders.

### Step 4: Confidence Interval

When 3+ comps are available, the confidence interval is derived from the **interquartile range (IQR)** of the comp multiples:

```
IQR = P75(multiples) - P25(multiples)
ci_width = IQR / predicted_multiple
```

This is more informative than a fixed ±30% band because it reflects the actual dispersion in market pricing for that sector/size.

## Why Median, Not Mean?

M&A multiples exhibit positive skew. A single 30x EBITDA outlier (e.g., a high-growth SaaS company in a mostly industrial comp set) would drag the mean up significantly while the median remains robust. In deal underwriting, anchoring to the central tendency of *typical* deals matters more than including outliers.

## Blending Comps with ML

Comps and ML models answer different questions about the right multiple:

- **Comps** provide an empirical market anchor: "What have buyers actually paid for similar companies?"
- **ML model** provides a company-specific adjustment: "Given this company's growth rate, margins, recurring revenue mix, and market conditions, how should the multiple differ from the sector average?"

When both are available, the engine produces a **weighted average** rather than choosing one over the other. Comp weight increases with the size and quality of the comp set (5+ comps = 60% comp weight, 3-4 = 50%, 1-2 = 35%). This reflects the principle that more comparable data points make the market signal more reliable.

When neither comps nor a trained model exist, the engine falls back to a sector default lookup table:

The sector default table maps normalized sector names to typical EV/EBITDA ranges:

| Sector | Default Multiple |
|--------|-----------------|
| SaaS | 14.0x |
| Software | 12.0x |
| Healthcare IT | 11.0x |
| Healthcare Services | 10.0x |
| Business Services | 9.0x |
| Financial Services | 10.0x |
| Technology Services | 10.0x |
| Consumer | 8.0x |
| Industrials | 7.5x |
| Manufacturing | 7.0x |
| Distribution | 7.0x |

## Limitations

1. **Stale comps:** Transaction data may be months or years old. Market conditions change, and yesterday's multiples may not reflect today's pricing.
2. **Selection bias:** Only completed transactions appear in databases. Companies that were marketed but didn't sell (often due to price gaps) are invisible.
3. **Hidden terms:** Reported enterprise value often doesn't capture earnouts, rollover equity, seller notes, or other structural features that affect effective price.
4. **Sector classification:** A company described as "technology-enabled distribution" could reasonably be classified under software, distribution, or logistics — each with very different typical multiples.

## Implementation

See `src/valuation/engine.py`, function `derive_multiple_from_comps()` and the `ShadowValuationEngine.value_company()` method. Comparable transactions are passed from the ingestion layer through the CLI (`src/cli.py`) to the valuation engine.
