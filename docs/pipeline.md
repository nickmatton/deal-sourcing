# Deal Sourcing Pipeline — Complete Technical Guide

This document describes every stage of the pipeline, what is implemented, how each piece works, where ML models belong, and where you can extend it.

---

## Architecture at a Glance

```
Stage 0A        Stage 0B         Stage 2           Stage 3A          Stage 3B
INGESTION  ──►  ENTITY     ──►  THESIS       ──►  SHADOW       ──►  ALPHA
                RESOLUTION      MATCHING          VALUATION         DETECTION
                                                       │                │
                                                       ▼                ▼
                                Stage 5           Stage 4          (merged)
                           ◄──  RAPID        ◄──  OUTREACH     ◄── ranked by
                                UNDERWRITING      ORCHESTRATION     alpha + IRR
                                     │
                                     ▼
                                Stage 7
                                FEEDBACK LOOP
```

**Cross-cutting layers** (implemented as shared modules, not pipeline stages):
- **Compliance & Governance** (`src/common/compliance.py`) — GDPR/CCPA checks, data retention, DSAR handling
- **Audit Trail** (`src/common/audit.py`) — immutable JSONL log of every model prediction, merge, and decision
- **Structured Logging** (`src/common/logging.py`) — stage/step context managers with timing, used everywhere

---

## How to Run

### CLI (primary entry point)

```bash
python -m src.cli run --sector "healthcare IT" --count 5
python -m src.cli run --thesis theses/healthcare-it-rollup.yaml --count 8
python -m src.cli run --sector "business services" --geography US --count 10 --log-level DEBUG
```

### Bloomberg-style Terminal UI

```bash
python -m src.terminal --sector "healthcare IT" --count 5
python -m src.terminal --thesis theses/healthcare-it-rollup.yaml
```

Both entry points run the same pipeline stages. The terminal provides a live curses UI with progress tracking, deal rankings table, drill-down detail views, and transaction comps display.

### Infrastructure (optional)

```bash
docker-compose up -d  # Postgres (pgvector), Redis, Kafka, MLflow
```

These services are defined but not required for the current pipeline. The pipeline runs fully in-memory with Claude CLI as the data source.

---

## Stage 0A: Data Ingestion

**Status: IMPLEMENTED**

**Purpose:** Fetch raw company and transaction data from external sources.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/ingestion/connectors/base.py` | Complete | Abstract base class defining the connector interface |
| `src/ingestion/connectors/claude_research.py` | Complete | Uses Claude CLI with web search to research real companies. Streams progress events, extracts JSON from LLM output. This is the primary connector for the pipeline. |
| `src/ingestion/connectors/yfinance_connector.py` | **Complete (free)** | Yahoo Finance via yfinance. Company profiles, financials, multiples, market data. No API key required. |
| `src/ingestion/connectors/sec_edgar.py` | **Complete (free)** | SEC EDGAR via edgartools. Full 10-K financial statements (revenue, EBITDA, margins) with multi-year history. No API key required. |
| `src/ingestion/connectors/fmp.py` | **Complete (free tier)** | Financial Modeling Prep. M&A transactions, company profiles, financial statements. Free API key (250 req/day). |
| `src/ingestion/connectors/pitchbook.py` | Skeleton | HTTP client for PitchBook API. Fully structured but untested (requires paid subscription). |
| `src/ingestion/connectors/crunchbase.py` | Skeleton | HTTP client for Crunchbase API. Partially structured (requires paid subscription). |
| `src/ingestion/normalizers/company.py` | Complete | Cleans raw records: normalizes domains, countries, computes EBITDA margin. |
| `src/ingestion/orchestration/assets.py` | Empty | Placeholder for Dagster asset definitions. |
| `src/data_viewer.py` | **Complete** | CLI tool to explore data from all sources. Commands: `yfinance`, `edgar`, `fmp`, `compare`, `sectors`. |

### Data Flow

```
ClaudeResearchConnector.fetch_companies()
  → prompt Claude CLI with sector/geography/count
  → stream JSON events (web searches, tool use, reasoning)
  → extract JSON response matching COMPANY_SCHEMA
  → return list[CompanyRaw]

ClaudeResearchConnector.fetch_transactions()
  → same pattern, using TRANSACTION_SCHEMA
  → return list[TransactionRecord]
```

### Schemas

- **`CompanyRaw`** (`src/common/schemas/ingestion.py`): Bronze-tier record with source attribution. Fields: name, domain, description, industry, NAICS, HQ location, founded year, employee count, estimated revenue/EBITDA, ownership type, funding history, executives.
- **`TransactionRecord`**: Historical M&A deal with target, buyer, deal type, sector, enterprise value, EV/EBITDA multiple, EV/Revenue multiple, target financials, deal date.
- **`CompanyNormalized`**: Silver-tier record after cleaning. Adds `entity_id`, `ebitda_margin`, normalized country codes.

### Data Viewer

Explore data from all connected sources without running the full pipeline:

```bash
python -m src.data_viewer yfinance AAPL CRM SNOW         # Company profiles from Yahoo Finance
python -m src.data_viewer edgar AAPL CRM                  # 10-K financials from SEC EDGAR
python -m src.data_viewer fmp --transactions --limit 20   # M&A deals from FMP (needs FMP_API_KEY)
python -m src.data_viewer fmp --financials AAPL CRM       # FMP company financials
python -m src.data_viewer compare AAPL CRM SNOW PLTR      # Side-by-side comparison table
python -m src.data_viewer sectors                          # Sector-level multiples
```

### Where to Extend

- **Wire free connectors into the pipeline:** The yfinance, SEC EDGAR, and FMP connectors can enrich Claude-researched companies with real financial data. For example, after Claude discovers a public comp, call `yfinance.get_company_profile()` to get verified revenue/EBITDA/multiples.
- **Build training data pipelines:** Use SEC EDGAR bulk financials + yfinance multiples to construct training datasets for the revenue, margin, and multiple prediction models.
- **Add more connectors:** SimilarWeb (web traffic), LinkedIn (headcount), G2 (reviews). Follow the `BaseConnector` interface.
- **Feature store integration:** The `feature_store/` module has empty `definitions/` and `transforms/` packages. Wire these up with Feast or a custom time-series store to serve features to models at inference time.

### Where ML Does NOT Belong

Ingestion is a data engineering stage. No ML models should live here. The connectors should fetch and normalize data as faithfully as possible. Transformation and scoring happen downstream.

---

## Stage 0B: Entity Resolution

**Status: PARTIALLY IMPLEMENTED**

**Purpose:** Deduplicate company records across data sources. The same company appears differently in PitchBook vs. Crunchbase vs. Claude research output. Entity resolution merges them into a single canonical entity with a stable ID.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/entity_resolution/blocking.py` | Complete | LSH-based blocking using prefix, domain, and geography keys. Reduces O(n^2) comparisons to O(n) by only comparing records in the same block. |
| `src/entity_resolution/matching.py` | Complete | Rule-based pairwise scoring using Jaro-Winkler name similarity (35%), domain exact match (30%), geography (15%), executive overlap (20%). |
| `src/entity_resolution/clustering.py` | Complete | Union-Find with path compression to cluster matched records into canonical entities. |
| `src/entity_resolution/engine.py` | Partial | `resolve_batch()` is complete (blocking → matching → clustering). But `resolve()` (used by the CLI) **does not actually deduplicate** -- it just assigns a new entity ID to every record. |

### How resolve_batch() Works

```
Records ──► LSHBlocker.get_candidate_pairs()
              │  (prefix blocking, domain blocking, geo blocking)
              ▼
            RuleBasedMatcher.match_candidates()
              │  (Jaro-Winkler + domain + geo + exec overlap)
              │  score >= 0.85 → auto merge
              │  score >= 0.60 → human review queue
              ▼
            EntityClusterer.cluster()
              │  (Union-Find connected components)
              ▼
            dict[record_index → entity_id]
```

### Known Gap

The CLI path calls `engine.resolve()` per-record, which just creates a new entity ID each time without comparing against previous records. This means if Claude returns "Acme Corp" and "ACME Corporation," they get separate entity IDs. To fix this, either:
1. Accumulate all raw records and call `resolve_batch()` at the end, or
2. Make `resolve()` actually compare the incoming record against the entity cache using the matcher.

### Where ML Could Help

Entity resolution is a good candidate for a **learned matcher** in a later phase. The current rule-based weights (name=0.35, domain=0.30, etc.) are hand-tuned. A trained model (logistic regression or gradient-boosted classifier) on labeled match/non-match pairs would learn the optimal weighting automatically. The codebase comment in `matching.py` notes: "Upgradeable to learned model in Phase 4."

---

## Stage 1: Signal Detection

**Status: IMPLEMENTED (model code), NOT WIRED INTO CLI**

**Purpose:** Predict which companies are likely to transact within 6-24 months. This is top-of-funnel scoring -- the goal is recall over precision, with rough size gating to avoid wasting cycles on companies outside the fund's mandate.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/signal_detection/features.py` | Complete | `SignalFeatures` dataclass with 23 features covering ownership/succession, growth trajectory, financial signals, advisor/strategic signals, sector signals, and behavioral signals. |
| `src/signal_detection/model.py` | Complete | `SellProbabilityModel`: XGBoost with custom focal loss (for <1% positive rate), isotonic calibration for reliable probabilities, SHAP-based explanations. Has `train()`, `predict()`, `explain()`, `save()`, `load()`. |
| `src/signal_detection/scoring.py` | Complete | `DealSignalScorer`: batch scoring orchestrator that runs the model, generates explanations, produces `DealSignal` output with trigger reasons, and logs to audit trail. |

### How It Would Work (Once Trained)

```
SignalFeatures (23 features per company)
  → SellProbabilityModel.predict()       # XGBoost + focal loss → raw logits
  → IsotonicRegression calibration        # → calibrated probabilities [0,1]
  → SellProbabilityModel.explain()        # SHAP TreeExplainer → top-3 drivers
  → DealSignal(sell_probability, trigger_reasons, estimated_scale, mandate_pass)
```

### Why This Stage is Skipped in the CLI

The model has no training data and no pretrained weights. Without a trained model, `predict()` raises `RuntimeError("Model not trained")`. The CLI bypasses this stage entirely and goes straight from ingestion to thesis matching.

### Where ML Lives

**This is the most important ML model in the pipeline.** It's a supervised binary classifier predicting a rare event (company transacts) from temporal features. The focal loss objective handles the severe class imbalance. See [Data Research](data-research.md) for what training data you need.

### Where to Extend

- **Wire into CLI:** After entity resolution, call `DealSignalScorer.score_batch()` and filter to companies above a threshold (e.g., `sell_probability > 0.3` from config).
- **Add temporal features:** The current feature set is a point-in-time snapshot. An LSTM or Transformer over event sequences (as described in the spec) would capture temporal patterns like "CFO departure followed by advisor engagement."
- **Graph features:** A GNN on the company relationship graph could detect contagion effects (PE firm acquiring one company in a sector increases transaction probability for peers).

---

## Stage 2: Thesis Matching

**Status: HARD FILTER IMPLEMENTED, SOFT SCORING PARTIAL**

**Purpose:** Match signal-positive companies to specific, codified investment theses. A thesis encodes the fund's investment strategy (sector focus, size range, ownership preference, deal type).

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/thesis_matching/thesis_schema.py` | Complete | `InvestmentThesis` Pydantic model + `ThesisStore` that loads from YAML files. |
| `src/thesis_matching/hard_filter.py` | Complete | Deterministic mandate gating: revenue range, geography, ownership preference, EBITDA margin floor, anti-patterns. Eliminates ~95% of universe cheaply. |
| `src/thesis_matching/semantic_matcher.py` | Complete | `SemanticMatcher` using sentence-transformers (`all-MiniLM-L6-v2`) for cosine similarity between thesis descriptions and company descriptions. |
| `theses/healthcare-it-rollup.yaml` | Complete | Example thesis definition. |

### How It Works

```
companies + thesis
  → apply_hard_filters()    # deterministic: revenue range, geography, ownership, margin floor
  │  passes = True/False
  │  gaps = ["Revenue below minimum", "Wrong geography", ...]
  ▼
  filter_universe()          # returns (passing_companies, rejected_with_reasons)
```

### Hard Filter Rules

| Rule | Source Field | Thesis Field |
|------|-------------|-------------|
| Revenue in range | `estimated_revenue_usd` | `revenue_range: [min, max]` |
| Geography match | `hq_country` | `geography: ["US", "CA"]` |
| Ownership type | `ownership_type` | `ownership_preference: ["founder", "family"]` |
| Margin above floor | `ebitda_margin` | `ebitda_margin_floor: 0.15` |
| Anti-pattern exclusion | Various | `anti_patterns: ["declining revenue"]` |

### What's Not Wired

The `SemanticMatcher` exists and works (embeds thesis descriptions and company descriptions, computes cosine similarity) but is **not called from the CLI or terminal**. It would produce soft fit scores for companies that pass the hard filter, enabling ranking by thesis fit rather than just pass/fail.

### Where ML Could Help

- **Soft scoring model:** The spec describes a two-stage model: hard filter (deterministic) + soft scoring (LLM for semantic matching + gradient-boosted model for quantitative features). The `SemanticMatcher` handles the LLM/embedding component. A gradient-boosted model could score quantitative fit (how close is the revenue to the thesis ideal range? how aligned is the growth rate?).
- **Thesis evolution tracking:** ML could identify which thesis parameters correlate with successful deals over time, suggesting thesis refinements.

### Where to Extend

- Wire `SemanticMatcher.rank_companies()` into the pipeline after hard filtering to produce ranked fit scores.
- Add a `ThesisMatch` schema output (already defined in `src/common/schemas/signals.py`) that captures fit score, rationale, and gap flags.
- Build the quantitative soft-scoring model once you have deal team feedback data ("did the team agree with the fit score?").

---

## Stage 3A: Shadow Valuation

**Status: IMPLEMENTED (with comp-based and sector-default fallbacks)**

**Purpose:** Attach a credible enterprise value range to every company that passes thesis matching. Converts "interesting company" into "potential deal."

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/valuation/engine.py` | Complete | `ShadowValuationEngine`: orchestrates Revenue → Margin → Multiple → EV. Supports blending comp-derived multiples with ML-predicted multiples. Falls back to sector defaults. |
| `src/valuation/revenue_estimator.py` | Complete (untrained) | XGBoost regressor on log-transformed revenue. 12 proxy features. |
| `src/valuation/margin_estimator.py` | Complete (untrained) | LightGBM regressor with Damodaran sector baselines. 8 features. |
| `src/valuation/multiple_predictor.py` | Complete (untrained) | LightGBM regressor for EV/EBITDA multiple. 10 features. |
| `src/valuation/confidence.py` | Complete (untrained) | Conformalized Quantile Regression (MAPIE) for distribution-free confidence intervals. |

### How It Works

```
Step 1: Revenue
  known_revenue provided? → use it
  revenue_estimator trained? → predict from proxy features
  neither? → raise ValueError

Step 2: Margin
  known_ebitda provided? → derive margin = EBITDA/Revenue
  margin_estimator trained? → predict margin, compute EBITDA = Revenue × margin
  neither? → use sector-median margin from Damodaran table (default 15%)

Step 3: Multiple (blended approach)
  comparable_transactions provided? → derive comp median EV/EBITDA
  multiple_predictor trained? → predict company-specific multiple
  both available? → weighted average (60/40 if 5+ comps, 50/50 if 3-4, 35/65 if 1-2)
  neither? → sector default lookup table (e.g., SaaS=14x, industrials=7.5x)

Step 4: Enterprise Value
  EV = EBITDA × Multiple × (1 - illiquidity_discount)
  CI width depends on data quality (Grade A: ±20%, B: ±30%, C: ±40%)
  If 3+ comps: CI refined using IQR of comp multiples
```

### Confidence Grading

| Grade | Criteria | CI Width |
|-------|----------|----------|
| A | 4+ data signals (known financials + trained models + comps) | ±20% |
| B | 2-3 data signals | ±30% |
| C | Sparse data (mostly defaults) | ±40% |

### Where ML Lives

Three ML models can improve this stage:

1. **`RevenueEstimator`** — predicts revenue from proxy features (employee count, web traffic, app downloads). Most impactful when you don't have known revenue.
2. **`MarginEstimator`** — adjusts sector-median margins using company-specific features. Most impactful for companies where EBITDA is unknown.
3. **`MultiplePredictor`** — predicts the company-specific multiple adjustment beyond the comp median. Always blended with comps (never overrides them).

All three have complete `train()`, `predict()`, `save()`, `load()` methods. They just need training data. See [Data Research](data-research.md).

Additionally, `ConformalizedQuantileRegressor` in `confidence.py` can replace the simple ±X% CI with distribution-free prediction intervals once trained. This uses MAPIE's CQR, which guarantees coverage without normality assumptions.

### Where to Extend

- Train the three estimators and pass them to `ShadowValuationEngine(revenue_estimator=..., margin_estimator=..., multiple_predictor=...)`.
- Add SHAP value extraction to populate `key_value_drivers` in the output (currently empty list).
- Implement the proxy DCF model described in the spec as a third valuation method to ensemble with comps and regression.

---

## Stage 3B: Alpha Detection

**Status: IMPLEMENTED**

**Purpose:** Identify companies where the buyer has a structural advantage -- where the deal is likely mispriced in the buyer's favor relative to a competitive auction.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/alpha_detection/scorer.py` | Complete | `AlphaScorer`: evaluates 5 alpha signals, produces weighted composite score, flags efficiently-priced targets. |

### How It Works

Five signal types are evaluated, each producing a strength score [0, 1]:

| Signal | Weight | What It Detects |
|--------|--------|----------------|
| Multiple Discount | 30% | Entry multiple below comp median |
| Operational Improvement | 25% | EBITDA margin below sector median (playbook upside) |
| Motivated Seller | 15% | Founder tenure 20+ yrs, PE hold pressure, family transition |
| Bilateral Probability | 15% | Owner-operator, sub-$50M revenue, small workforce |
| Market Timing | 15% | Sector multiples depressed vs. historical norms |

```
alpha_score = Σ(signal_strength × weight) / Σ(all_weights)

alpha < 0.20 → efficiently_priced = True  (no buyer edge, deprioritize)
alpha >= 0.20 → efficiently_priced = False (structural advantage exists)
```

### Where ML Could Help (Later)

The current scorer is rule-based. Once you have outcome data (did "high alpha" deals actually produce better returns than "low alpha" deals?), you could train a model to learn which signal combinations actually predict outsized returns. This is a long-horizon feedback loop (3-7 years for IRR realization).

### Where to Extend

- Add **buy-and-build premium** detection: if the fund already owns a platform in this sector, an add-on acquisition has synergy value above standalone.
- Add **information asymmetry** detection: when a company's revenue model is misclassified in databases (e.g., recurring SaaS revenue tagged as "consulting"), the market undervalues it.

---

## Stage 4: Outreach Orchestration

**Status: IMPLEMENTED (module exists), NOT WIRED INTO CLI**

**Purpose:** Convert prioritized targets into conversations. Generate personalized outreach drafts calibrated by seller expectations.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/outreach/drafting.py` | Complete | `OutreachDrafter`: LLM-powered outreach email generation with thesis-specific narrative, warm path references, tone calibration. |
| `src/common/schemas/outreach.py` | Complete | `OutreachDraft`, `OutreachEvent`, `FounderExpectation`, `ToneRecommendation`, `OutreachChannel`, `PipelineStage` schemas. |

### How It Works

```
build_outreach_prompt()
  → Thesis fit rationale + company signals + founder name + warm path + tone
  → OUTREACH_SYSTEM_PROMPT (PE professional persona, no valuation numbers)
  → Claude API call (or placeholder if LLM not configured)
  → OutreachDraft(subject, body, channel, tone, approved=False)
```

Tone options: `PREMIUM_BUYER`, `DISCIPLINED_VALUE`, `PARTNERSHIP`, `GROWTH_ACCELERATION`.

### What's Not Wired

The drafter exists but is never called from the CLI or terminal pipeline. After underwriting, high-priority targets should automatically get draft outreach generated for human review.

### Where ML Could Help

- **Tone calibration model:** The spec describes a Founder Price Expectation Model that predicts seller price expectations and recommends tone accordingly. Currently the tone is hardcoded.
- **Channel optimization:** Once you have outreach outcome data (response rates by channel, tone, relationship path), a model could optimize channel/tone selection.

### Where to Extend

- Wire into CLI after underwriting: for `screening_decision == "priority"`, auto-generate outreach drafts.
- Implement the `FounderExpectation` model to predict seller sophistication and expected ask price.
- Add outreach tracking (`OutreachEvent`) to capture response data for the feedback loop.

---

## Stage 5: Rapid Underwriting (Monte Carlo)

**Status: FULLY IMPLEMENTED**

**Purpose:** Determine whether a target can meet the fund's return threshold before committing significant time and capital. Answers: "Is this worth pursuing aggressively?"

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/underwriting/monte_carlo.py` | Complete | `MonteCarloSimulator`: vectorized 10,000-simulation IRR/MOIC engine with PERT distributions, real tornado-chart sensitivity analysis, screening decisions. |
| `src/common/schemas/underwriting.py` | Complete | `LBOAssumptions`, `IRRDistribution`, `Sensitivity`, `UnderwritingResult` schemas. |

### How It Works

```
Sample from distributions:
  Entry EBITDA     ~ Normal(μ, σ)
  Entry Multiple   ~ Triangular(low, mode, high)
  Revenue Growth   ~ Normal(μ, σ)
  Margin Improvement ~ Triangular(low, mode, high)
  Exit Multiple    ~ PERT(bear, base, bull)     # Beta-parameterized, fatter tails
  Leverage         ~ Uniform(low, high)
  Hold Period      ~ Discrete{3, 4, 5, 6, 7} years

Vectorized computation (all 10K scenarios at once):
  Entry EV        = Entry EBITDA × Entry Multiple
  Equity Invested = Entry EV × (1 - Debt/EV)
  Exit EBITDA     = Entry EBITDA × (1 + g)^hold × (1 + Δmargin)
  Exit EV         = Exit EBITDA × Exit Multiple
  Debt at Exit    = Debt × (1 + r)^hold
  Equity at Exit  = max(Exit EV - min(Debt at Exit, Exit EV), 0)
  MOIC            = Equity at Exit / Equity Invested
  IRR             = MOIC^(1/hold) - 1

Screening decision:
  P(IRR > 20%) < 30%  → auto_reject
  P(IRR > 25%) > 40%  → priority
  otherwise            → pursue
```

### Sensitivity Analysis (Tornado Chart)

Real OAT (one-at-a-time) perturbation, not hardcoded offsets:
1. Compute base-case IRR with all parameters at median/mode.
2. For each parameter, swing to low and high bounds while holding others at base.
3. Measure the IRR delta. Sort by impact descending.

Parameters analyzed: entry multiple, revenue growth, exit multiple, margin improvement, leverage.

### Where ML Does NOT Belong

Monte Carlo simulation is the correct tool for underwriting. The uncertainty is in the **assumptions** (what growth rate to use, what exit multiple is realistic), not in the model structure. ML's role is upstream -- providing better assumptions (revenue estimates, margin estimates, multiple predictions) that feed into the simulation.

### Where to Extend

- Add **debt amortization** and **interim cash flows** (dividends, management fees) for more realistic LBO modeling.
- Add **add-on acquisition scenarios** for buy-and-build theses.
- Wire the simulation output to the `ICMemo` schema for automated IC preparation.

---

## Stage 6: IC Preparation

**Status: SCHEMA ONLY**

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/ic_prep/__init__.py` | Empty | No implementation. |
| `src/common/schemas/underwriting.py` | Complete | `ICMemo` schema with sections (investment thesis, company overview, financial summary, valuation analysis, key risks, value creation plan). |

### Where to Extend

Implement an IC memo generator that synthesizes all upstream outputs (company profile, thesis fit, valuation, alpha signals, underwriting results) into a structured memo. This is a natural LLM task -- generate narrative sections from structured data.

---

## Stage 7: Feedback Loop

**Status: PARTIALLY IMPLEMENTED (drift detection only)**

**Purpose:** Every deal outcome generates training signal. The feedback loop ensures all models improve over time.

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `src/feedback/drift_detection.py` | Complete | `DriftDetector`: PSI (Population Stability Index) for score distribution drift, Expected Calibration Error for probability calibration monitoring. |
| `src/common/schemas/feedback.py` | Complete | `OutcomeFeedback`, `ModelPerformanceMetric`, `RetrainingTrigger` schemas. Feedback types: outreach outcome, deal team decision, pipeline progression, cleared price, post-acquisition. |

### Drift Detection

```
compute_psi(reference_scores, current_scores)
  PSI > 0.1 → significant drift (alert)
  PSI > 0.2 → major shift (trigger retraining)

check_calibration(predicted_probs, actual_outcomes)
  ECE measures predicted vs. observed frequency alignment
  High ECE → model probabilities are no longer reliable
```

### What's Not Wired

- No mechanism to capture actual deal outcomes and feed them back.
- No retraining pipeline (the `quarterly_retraining` schedule in `pipelines/definitions.py` is just a string list).
- No champion/challenger deployment.

### Where to Extend

- Implement outcome capture: when a deal closes (or doesn't), record the actual price, IRR, and which signals were correct.
- Build retraining triggers: when PSI > threshold or when N new labeled outcomes are available, retrain the relevant model.
- Implement A/B testing: route a fraction of scoring through a challenger model and compare performance.

---

## Pipeline Orchestration

**Status: ASPIRATIONAL (Dagster definitions exist as string lists only)**

### What Exists

| File | Status | Description |
|------|--------|-------------|
| `pipelines/definitions.py` | Placeholder | Lists of asset names and cron schedules. No actual Dagster `@asset` or `@job` decorators. |

### Defined Schedules

| Schedule | Cron | Assets |
|----------|------|--------|
| Daily ingestion | 6 AM daily | Raw data fetch, entity resolution, feature store refresh, event-triggered rescoring |
| Weekly scoring | 8 AM Mondays | Full universe signal scores, thesis match rankings, shadow valuations, alpha scores, outreach queue |
| Quarterly retraining | 1st of every 3rd month | Retrain sell probability, revenue estimator, margin estimator, multiple predictor |

### Where to Extend

Convert the string lists into real Dagster `@asset` definitions with:
- Dependency tracking (valuations depend on entity resolution, etc.)
- Freshness policies (alert if data is stale)
- Retry logic (external API failures)
- Partitioning (process companies in batches)

---

## Where ML Models Should Live — Summary

| Stage | Model | Purpose | Training Data Required | Status |
|-------|-------|---------|----------------------|--------|
| **1. Signal Detection** | `SellProbabilityModel` (XGBoost + focal loss) | Predict sell probability from 23 temporal/structural features | Transaction outcomes + company features over 2-3 years | Code complete, no training data |
| **2. Thesis Matching** | `SemanticMatcher` (sentence-transformers) | Soft-score thesis fit using embedding similarity | Pre-trained embeddings (no fine-tuning needed) | Code complete, not wired in |
| **3A. Valuation** | `RevenueEstimator` (XGBoost) | Estimate revenue from proxy features | Known revenues + proxy features (Inc 5000, PitchBook) | Code complete, no training data |
| **3A. Valuation** | `MarginEstimator` (LightGBM) | Estimate EBITDA margin beyond sector median | Known margins (PitchBook, public filings) | Code complete, no training data |
| **3A. Valuation** | `MultiplePredictor` (LightGBM) | Predict company-specific EV/EBITDA adjustment | Deal multiples + target characteristics (PitchBook) | Code complete, no training data |
| **3A. Valuation** | `ConformalizedQuantileRegressor` (MAPIE) | Distribution-free confidence intervals | Same as revenue/margin estimator | Code complete, no training data |
| **0B. Entity Resolution** | Learned matcher (future) | Replace hand-tuned match weights | Labeled match/non-match pairs | Not started |
| **3B. Alpha Detection** | Outcome-weighted scorer (future) | Learn which alpha signals predict real returns | Deal outcomes over 3-7 years | Not started |
| **4. Outreach** | Tone/channel optimizer (future) | Optimize outreach for response rate | Outreach outcome data | Not started |

---

## Cross-Cutting: Configuration

**File:** `src/common/config.py`

All settings are managed through `PipelineSettings` (Pydantic Settings with `.env` file support):

| Setting | Default | Environment Variable |
|---------|---------|---------------------|
| `sell_probability_threshold` | 0.3 | — |
| `thesis_fit_score_threshold` | 0.5 | — |
| `irr_hurdle_rate` | 0.20 | — |
| `irr_priority_threshold` | 0.25 | — |
| `illiquidity_discount_low` | 0.15 | — |
| `illiquidity_discount_high` | 0.30 | — |
| `max_outreach_batch_size` | 20 | — |
| LLM API key | "" | `LLM_API_KEY` |
| Database URL | localhost:5432 | `DB_HOST`, `DB_PORT`, etc. |
| MLflow tracking URI | localhost:5000 | `MLFLOW_TRACKING_URI` |

---

## Cross-Cutting: Audit Trail

**File:** `src/common/audit.py`

Every model prediction, entity merge, score generation, and outreach event is logged to `audit_trail.jsonl` as an append-only record:

```json
{
  "timestamp": "2024-03-15T10:30:00+00:00",
  "action": "score_generated",
  "actor": "shadow_valuation",
  "entity_id": "abc-123",
  "model_version": "v1",
  "details": {"ev_point_estimate": 38400000, "implied_multiple": 12.0},
  "stage": "3A"
}
```

Actions: `model_prediction`, `human_override`, `outreach_sent`, `deal_stage_change`, `data_access`, `entity_merge`, `entity_split`, `score_generated`, `memo_generated`.

---

## File Map

```
src/
├── __main__.py                          # Entry point
├── cli.py                               # CLI pipeline runner (all stages wired)
├── data_viewer.py                       # Data exploration CLI (yfinance/edgar/fmp/compare/sectors)
├── terminal/
│   └── app.py                           # Bloomberg-style curses UI
├── common/
│   ├── config.py                        # PipelineSettings (Pydantic Settings)
│   ├── logging.py                       # Structured logging (structlog)
│   ├── audit.py                         # Append-only audit trail
│   ├── compliance.py                    # GDPR/CCPA compliance checks
│   ├── entity.py                        # CanonicalEntity, SourceRecord
│   └── schemas/
│       ├── ingestion.py                 # CompanyRaw, CompanyNormalized, TransactionRecord
│       ├── signals.py                   # DealSignal, ThesisMatch
│       ├── valuation.py                 # ShadowValuation, AlphaScore, AlphaSignal
│       ├── underwriting.py              # LBOAssumptions, UnderwritingResult, ICMemo
│       ├── outreach.py                  # OutreachDraft, OutreachEvent, FounderExpectation
│       └── feedback.py                  # OutcomeFeedback, ModelPerformanceMetric
├── ingestion/
│   ├── connectors/
│   │   ├── base.py                      # BaseConnector ABC
│   │   ├── claude_research.py           # Claude CLI connector (primary)
│   │   ├── yfinance_connector.py         # Yahoo Finance (free, no API key)
│   │   ├── sec_edgar.py                 # SEC EDGAR via edgartools (free, no API key)
│   │   ├── fmp.py                       # Financial Modeling Prep (free tier, 250 req/day)
│   │   ├── pitchbook.py                 # PitchBook API (skeleton, paid)
│   │   └── crunchbase.py                # Crunchbase API (skeleton, paid)
│   ├── normalizers/
│   │   └── company.py                   # Raw → Normalized transformation
│   └── orchestration/
│       └── assets.py                    # Dagster assets (empty)
├── entity_resolution/
│   ├── blocking.py                      # LSH blocking (prefix, domain, geo)
│   ├── matching.py                      # Rule-based pairwise scoring
│   ├── clustering.py                    # Union-Find clustering
│   └── engine.py                        # Orchestrator (blocking → matching → clustering)
├── signal_detection/
│   ├── features.py                      # SignalFeatures (23 features)
│   ├── model.py                         # SellProbabilityModel (XGBoost + focal loss)
│   └── scoring.py                       # DealSignalScorer (batch orchestrator)
├── thesis_matching/
│   ├── thesis_schema.py                 # InvestmentThesis + ThesisStore (YAML)
│   ├── hard_filter.py                   # Deterministic mandate gating
│   └── semantic_matcher.py              # Sentence-transformer similarity
├── valuation/
│   ├── engine.py                        # ShadowValuationEngine (orchestrator)
│   ├── revenue_estimator.py             # XGBoost revenue predictor
│   ├── margin_estimator.py              # LightGBM margin predictor + sector baselines
│   ├── multiple_predictor.py            # LightGBM multiple predictor
│   └── confidence.py                    # MAPIE Conformalized Quantile Regression
├── alpha_detection/
│   └── scorer.py                        # AlphaScorer (5-signal composite)
├── outreach/
│   └── drafting.py                      # LLM-powered outreach generation
├── underwriting/
│   └── monte_carlo.py                   # Vectorized Monte Carlo IRR simulator
├── feature_store/
│   ├── definitions/                     # Empty (Feast feature definitions)
│   └── transforms/                      # Empty (feature transforms)
├── feedback/
│   └── drift_detection.py              # PSI + calibration monitoring
└── ic_prep/                             # Empty (IC memo generation)

pipelines/
└── definitions.py                       # Dagster schedule/asset definitions (strings only)

theses/
└── healthcare-it-rollup.yaml            # Example investment thesis

tests/
└── unit/
    ├── test_monte_carlo.py              # IRR computation + simulation tests
    ├── test_entity_resolution.py        # Blocking, matching, clustering tests
    ├── test_hard_filter.py              # Thesis filter tests
    ├── test_drift_detection.py          # PSI + calibration tests
    ├── test_schemas.py                  # Schema validation tests
    └── test_logging.py                  # Logging infrastructure tests

docs/
├── pipeline.md                          # This file
├── data-research.md                     # Training data acquisition guide
├── shadow-valuation-engine.md           # Valuation methodology
├── comparable-transactions.md           # Comp-based multiple derivation
├── monte-carlo-irr-simulation.md        # Monte Carlo underwriting
├── sensitivity-analysis-tornado-chart.md # Tornado chart methodology
└── alpha-detection.md                   # Mispricing scoring methodology
```
