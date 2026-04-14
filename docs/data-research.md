# Data Sources for Model Training

## Current State

There is **no training data in this repository**. The `models/` and `notebooks/` directories are empty. Every ML model (`SellProbabilityModel`, `RevenueEstimator`, `MarginEstimator`, `MultiplePredictor`) has `train()` methods but no data to call them with. The pipeline currently works because every model path falls through to hardcoded defaults, sector lookup tables, or Claude-researched estimates.

---

## Signal Detection (Sell Probability Model)

**What this model does:** Predicts the probability that a private company will transact (be sold, seek investment) within a 6-24 month window. This is a binary classification problem with severe class imbalance (<1% positive rate).

**Why ML matters here:** The temporal patterns that precede a transaction (CFO departure + advisor hire + debt maturity within 18 months) are non-obvious and compound. Rule-based approaches can catch individual signals but miss the multi-signal combinations that actually predict deals.

### Training Data Needed

| Data Need | What It Provides | Sources | Estimated Cost |
|-----------|-----------------|---------|---------------|
| **Historical M&A transactions** (labels) | "Did this company transact?" -- the binary outcome you're predicting | PitchBook, Capital IQ, Refinitiv | $50-200K/yr subscription |
| **Founder/owner demographics** | `founder_age`, `owner_tenure_years`, succession plan indicators | LinkedIn API, PitchBook People data | Part of PitchBook sub |
| **Employee headcount time series** | `employee_growth_30d`, `employee_growth_90d` -- hiring velocity changes | LinkedIn Talent Insights, Revelio Labs, Coresignal | $20-80K/yr |
| **Web traffic time series** | `web_traffic_growth_90d` -- digital footprint changes | SimilarWeb, Semrush | $10-30K/yr |
| **News/filings NLP signals** | `advisor_engagement_detected`, `leadership_change_6m`, `external_ceo_appointed` | Factiva, Bloomberg News, SEC EDGAR (free), NLP pipeline | $20-50K/yr for news; EDGAR is free |
| **Debt/credit data** | `debt_maturity_months` -- capital structure pressure | Capital IQ, Moody's, S&P credit databases | Part of Capital IQ sub |
| **Sector M&A activity counts** | `sector_ma_activity_12m` -- sector wave detection | PitchBook (aggregated from transaction data) | Same sub |
| **Review/product signals** | `review_volume_growth_90d` -- product traction changes | G2 API, Capterra | Free-$10K/yr |

### Minimum Viable Training Set

You need ~5,000+ companies with known "did/didn't transact" outcomes over a 2-3 year window. Given the <1% positive rate, that means you need at least 50 confirmed transactions as positive labels. The focal loss objective in the model code (`signal_detection/model.py`) is designed for exactly this imbalance.

**PitchBook alone** gets you transactions (labels) + company profiles + executive data + sector activity. It is the single highest-leverage subscription for this model.

### Label Latency

Signal detection labels take 6-24 months to materialize (you predict a future transaction, then wait to see if it happens). This means:
- Your first model version will train on purely historical data (companies that did/didn't transact in 2020-2023).
- The feedback loop won't produce retraining signal for 6-24 months after deployment.
- Quarterly retraining is appropriate once outcome data starts flowing.

---

## Revenue Estimation

**What this model does:** Estimates annual revenue for private companies that don't publicly disclose financials, using proxy features (employee count, web traffic, app downloads, etc.). This is a regression problem.

**Why ML matters here:** The relationship between proxies and revenue is sector-dependent and non-linear. A 100-person SaaS company has very different revenue than a 100-person manufacturing company. An ML model captures these interactions automatically.

### Training Data Needed

| Data Need | What It Provides | Sources | Estimated Cost |
|-----------|-----------------|---------|---------------|
| **Actual private company revenue** (labels) | The revenue number you're predicting | PitchBook (PE-backed), PrivCo, Inc. 5000 list, public filings | $30-100K/yr |
| **Employee count** | Strongest single proxy (~$130K/employee for SaaS, varies) | LinkedIn, PitchBook, Crunchbase | Overlap with above |
| **Web traffic metrics** | `web_traffic_monthly`, `web_traffic_growth_90d` | SimilarWeb API | $10-30K/yr |
| **App download data** | `app_downloads_monthly` -- mobile-first businesses | Sensor Tower, data.ai | $15-40K/yr |
| **Software review data** | `review_count`, `review_growth_90d` -- B2B SaaS adoption | G2 API, Capterra scrape | Free-$10K/yr |
| **Funding history** | `funding_total_usd` -- capital deployed as scale indicator | Crunchbase (free tier), PitchBook | Free-$50K/yr |

### Free/Cheap Bootstrapping

The **Inc. 5000 list** publishes revenue for ~5,000 private US companies annually and is essentially free. Combine with:
- **Crunchbase free tier:** employee count, funding, founding date
- **SimilarWeb free API:** limited web traffic data

This gives you enough to train a basic revenue estimator. The model trains on `log(1 + revenue)` to handle the right-skewed distribution (see `revenue_estimator.py`).

---

## Margin Estimation (EBITDA)

**What this model does:** Estimates EBITDA margin for private companies. This is the scarcest data point because private companies almost never disclose profitability.

**Why ML matters here:** A flat 15% default margin (the current fallback) is wrong for most companies. SaaS companies run 20-30% margins; distribution companies run 5-8%. The ML model adjusts for sector, growth stage, scale, and business model.

### Training Data Needed

| Data Need | What It Provides | Sources | Estimated Cost |
|-----------|-----------------|---------|---------------|
| **EBITDA margins for private companies** (labels) | The margin you're predicting | PitchBook (some PE-backed), PrivCo, Sageworks/Vertical IQ, RMA Annual Statement Studies | $20-80K/yr |
| **Public company financials** (transfer learning) | Train on public, transfer to private with sector adjustment | SEC EDGAR XBRL (free), Capital IQ, Bloomberg | Free-$100K/yr |
| **Company characteristics** | Sector, growth rate, employee count, customer concentration | Same sources as revenue estimation | Overlap |

### Free Bootstrapping Strategy

**Train on public company data from SEC filings (free via EDGAR/XBRL)**, then apply to private companies with a sector adjustment. Public SaaS companies report margins in 10-K filings; applying those distributions to private SaaS companies with similar characteristics is imperfect but substantially better than a flat 15% default.

The current code already has sector-median baselines from Damodaran research in `margin_estimator.py`. The ML model's job is to adjust these baselines using company-specific features.

---

## Multiple Prediction

**What this model does:** Predicts the appropriate EV/EBITDA multiple for a target company. This is the easiest model to train because deal multiples are directly reported in transaction databases.

**Why ML matters here:** The "right" multiple is not just the sector median -- it's a function of growth rate, margin quality, recurring revenue %, size, customer concentration, and market conditions, all interacting non-linearly. Comps give the market baseline; the ML model captures the company-specific adjustment. See the [Shadow Valuation Engine docs](shadow-valuation-engine.md) for how these are blended.

### Training Data Needed

| Data Need | What It Provides | Sources | Estimated Cost |
|-----------|-----------------|---------|---------------|
| **EV/EBITDA multiples** (labels) | The multiple from each completed transaction | PitchBook, Capital IQ, Refinitiv | Same subs as above |
| **Target company characteristics at deal time** | Growth rate, margin, size, sector, recurring revenue % | Same sources, point-in-time snapshots | Same subs |
| **Market conditions at deal time** | Credit spreads, IPO volume, sector indices | FRED (free), Bloomberg | Free-$50K/yr |

**PitchBook alone covers this entirely** -- every deal record includes the multiple, the target's financials, and the deal context.

---

## Recommended Data Acquisition Strategy

### Tier 1: Minimum Viable (Budget: ~$0-5K/yr)

Enough to train basic revenue and margin estimators.

| Source | Cost | What You Get |
|--------|------|-------------|
| Inc. 5000 list | Free | ~5,000 private company revenues per year |
| Crunchbase free tier | Free | Employee count, funding, founding date, sector |
| SEC EDGAR XBRL | Free | Public company financials for transfer learning |
| SimilarWeb free API | Free | Limited web traffic data |
| FRED | Free | Macro/market condition features |

### Tier 2: Core Intelligence (Budget: ~$100-150K/yr)

Enough to train all four models with reasonable quality.

| Source | Cost | What You Get |
|--------|------|-------------|
| **PitchBook** | ~$80-120K/yr | Transactions (labels), multiples, company profiles, executive data, sector activity |
| **SimilarWeb Pro** | ~$20K/yr | Web traffic time series for revenue proxy |
| Crunchbase Pro | ~$5K/yr | Broader company coverage |

PitchBook is the single highest-leverage subscription. It covers transaction labels for signal detection, deal multiples for the multiple predictor, partial company financials, and executive data.

### Tier 3: Full Coverage (Budget: ~$300-500K/yr)

Comprehensive feature coverage for all models plus alternative data.

| Source | Cost | What You Get |
|--------|------|-------------|
| PitchBook | ~$100K/yr | Core deal data |
| Capital IQ | ~$80K/yr | Deeper financials, debt data, credit |
| LinkedIn Talent Insights | ~$30K/yr | Employee growth time series |
| SimilarWeb Pro | ~$20K/yr | Web traffic |
| Revelio Labs / Coresignal | ~$40K/yr | Granular workforce analytics |
| Sensor Tower | ~$25K/yr | App download data |
| Factiva / Bloomberg News | ~$30K/yr | News NLP for event detection |

---

## Building the Training Pipeline

Once you have data, the training workflow would be:

1. **Ingest historical data** through the existing connectors (`PitchBookConnector`, `CrunchbaseConnector`) into the feature store.
2. **Label construction:** Join company features with transaction outcomes (did this company appear as a target in a deal within 6/12/24 months of the feature snapshot?).
3. **Train/validate split:** Time-based split (train on 2020-2022, validate on 2023, test on 2024) to prevent lookahead bias.
4. **Model training:** Call the existing `.train()` methods on `SellProbabilityModel`, `RevenueEstimator`, `MarginEstimator`, `MultiplePredictor`.
5. **Model storage:** Save to `models/` directory and track with MLflow (docker-compose already includes an MLflow service).
6. **Integration:** Pass trained models into the engine constructors (`ShadowValuationEngine(revenue_estimator=..., margin_estimator=..., multiple_predictor=...)`).

The code for steps 4-6 is already written -- the missing piece is steps 1-3 (getting data and constructing training sets).
