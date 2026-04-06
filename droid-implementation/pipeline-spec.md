# AI-Powered Private Equity Deal Sourcing Pipeline — Technical Specification

## Executive Summary

This document specifies a full-stack, AI/ML-powered deal sourcing pipeline for a private equity firm. The pipeline ingests structured and alternative data, identifies companies likely to transact, matches them to investment theses, attaches credible valuations, detects mispricing, orchestrates outreach, underwrites deals rapidly, and optimizes bid strategy — all while continuously learning from outcomes.

### Key Changes from Initial Design

- **Added**: Entity Resolution Engine (Stage 0B) — the single most common technical failure in PE data pipelines
- **Added**: Market Context Layer (Stage 0C) — macro/sector regime awareness that conditions every downstream model
- **Added**: Compliance & Governance Layer — cross-cutting concern, not a stage, but essential for automated outreach and data scraping
- **Added**: IC Preparation Automation (Stage 6B) — LLM-powered memo generation that bridges underwriting and deal team decision-making
- **Merged**: Shadow Valuation (original Stage 3) and Alpha Filtering (original Stage 4) into a single Valuation & Alpha Detection stage — these are tightly coupled and share the same model infrastructure
- **Removed**: Bid Strategy Optimization as a standalone stage — folded into Rapid Underwriting, since bid strategy is a direct output of the IRR simulator with founder expectation inputs
- **Strengthened**: Feedback Loop with specific metrics, retraining cadence, and drift detection

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COMPLIANCE & GOVERNANCE LAYER                    │
│         (Data privacy, audit trails, MNPI controls, GDPR)          │
└─────────────────────────────────────────────────────────────────────┘
       │           │           │           │           │
┌──────┴───┐ ┌─────┴────┐ ┌────┴─────┐ ┌────┴────┐ ┌────┴────────┐
│  Stage 0 │→│ Stage 1  │→│ Stage 2  │→│ Stage 3 │→│  Stage 4    │
│   Data   │ │  Signal  │ │  Thesis  │ │ Valuation│ │  Outreach   │
│Ingestion │ │Detection │ │ Matching │ │ & Alpha │ │& Relationship│
└──────────┘ └──────────┘ └──────────┘ └─────────┘ └─────────────┘
       │                                      │           │
       │                                ┌─────┴────┐ ┌────┴────────┐
       │                                │ Stage 5  │→│  Stage 6    │
       │                                │  Rapid   │ │IC Prep &    │
       │                                │Underwrite│ │Bid Strategy │
       │                                └──────────┘ └─────────────┘
       │                                      │
       └──────────────────────────────────────┘
                    FEEDBACK LOOP (Stage 7)
```

---

## Stage 0: Data Ingestion & Foundation

The entire pipeline depends on this layer being robust. It has three sub-components.

### 0A: Data Ingestion

**Goal**: Ingest, normalize, and refresh all data streams into a unified storage layer.

**Data Streams**:

| Stream | Sources | Refresh Cadence | Key Signals |
|--------|---------|-----------------|-------------|
| Company fundamentals | PitchBook, Crunchbase, PrivCo, SEC/EDGAR | Daily (market), Weekly (enrichment) | Revenue, EBITDA, funding history, ownership |
| Talent signals | LinkedIn (licensed), Indeed, Glassdoor | Weekly | Hiring velocity, exec churn, role mix (sales vs. eng), seniority distribution |
| Financial proxies | SimilarWeb, Sensor Tower, G2/Capterra, credit card panels (Second Measure) | Daily-Weekly | Web traffic, app downloads/DAU, review volume/sentiment, transaction proxies |
| Transaction data | PitchBook, BIZCOMPS, DealStats, Capital IQ | Daily | Historical deals, cleared multiples, buyer profiles, deal structure |
| News & events | News APIs, press releases, SEC filings, court records | Real-time (streaming) | M&A announcements, leadership changes, restructuring language, litigation |
| Relationship data | CRM (Affinity/DealCloud), email/calendar sync | Continuous | Interaction history, relationship warmth, warm intro paths |
| Market context | Fed data, public indices, Damodaran multiples, credit spreads | Daily | Sector multiples, credit conditions, IPO/M&A volume |

**Architecture**:
- **Storage**: Data lakehouse (Databricks or Snowflake) with bronze/silver/gold medallion architecture
- **Ingestion**: Event-driven (Kafka/Kinesis) for real-time streams; scheduled batch for API-based sources
- **Orchestration**: Airflow or Dagster for pipeline scheduling, monitoring, and retry logic

**Output**: Raw and cleaned data in the lakehouse, ready for entity resolution.

---

### 0B: Entity Resolution Engine

**Goal**: Create a single, deduplicated company entity graph across all data sources.

**Why this is critical**: Private companies have no universal identifier. The same company appears differently across PitchBook, Crunchbase, LinkedIn, and scraped sources. Entity resolution failure is the #1 technical failure mode in PE data pipelines — it causes duplicate scoring, missed matches, and corrupted training data.

**Approach**:
- **Blocking**: Reduce candidate pairs using locality-sensitive hashing on company name + domain + geography
- **Matching**: Learned similarity model (fine-tuned transformer or Siamese network) on:
  - Company name (fuzzy matching, abbreviation handling)
  - Domain/URL
  - Address/geography
  - Founder/executive names
  - Industry classification
- **Clustering**: Connected components with confidence thresholds — high-confidence matches merge automatically; ambiguous matches flag for human review
- **Persistent ID**: Each resolved entity gets a stable internal UUID that all downstream systems reference

**Output**: Unified company entity graph with stable IDs, linking all source records.

---

### 0C: Feature Store

**Goal**: Serve ML-ready features at batch and real-time latency for all downstream models.

**Implementation**: Feast (open source) or Tecton, backed by the lakehouse.

**Feature Groups**:

| Group | Example Features | Update Frequency |
|-------|-----------------|------------------|
| Firmographics | Revenue estimate, employee count, age, geography, NAICS | Weekly |
| Ownership | Owner type (founder/PE/family), hold duration, cap table signals | Weekly |
| Growth trajectory | 30/60/90d hiring velocity, web traffic trend, review growth rate | Daily |
| Transaction signals | Advisor engagement, leadership changes, debt maturity proximity | Daily |
| Behavioral | Website redesigns, tech stack changes, job posting pattern shifts | Weekly |
| Financial proxies | Estimated revenue, estimated EBITDA, margin proxy | Weekly |
| Market context | Sector median multiples, public comp performance, credit spreads | Daily |
| Relationship | Warmth score to target, degrees of separation, last interaction date | Continuous |

**Critical requirements**:
- **Point-in-time correctness**: Feature snapshots must be time-stamped for training to avoid data leakage
- **Feature versioning**: Track schema changes and feature drift
- **Lineage**: Every feature traceable to source data for audit

---

## Stage 1: Deal Signal Detection (Top-of-Funnel)

**System**: Latent Deal Signal Engine
**Goal**: Score every company in the universe on probability of transacting within 12-24 months.

### Model Architecture

**Primary model**: Gradient-boosted trees (XGBoost or LightGBM) — proven best-in-class for tabular PE data, achieving ~84% accuracy and 0.90 AUC on propensity scoring benchmarks.

**Target variable**: Binary — did the company transact (change of control, recapitalization, or significant minority sale) within the prediction window? Trained on historical deal data.

**Top features (ranked by SHAP importance from literature)**:

1. **Owner age / tenure** — founder/owner approaching 65+ with no succession plan is the strongest single signal in the lower-middle-market
2. **PE hold duration** — portfolio companies held >5 years are statistically more likely to exit in the next 12 months
3. **Hiring velocity change** — a hiring freeze after sustained growth is a conspicuous pre-sale signal
4. **Advisor engagement** — detectable via LinkedIn connections to bankers, hiring "VP Corporate Development" roles, broker platform listings
5. **Debt maturity proximity** — capital structure pressure forces action
6. **Leadership transitions** — new external CEO/CFO appointments often precede strategic changes
7. **Industry consolidation activity** — 3+ comparable transactions in a sector within 12 months increases remaining companies' sell probability
8. **Revenue growth plateau** — 2+ years of flat growth with strong margins signals "peak value" perception
9. **Website/branding changes** — companies preparing for sale often refresh digital presence
10. **Technology stack maturation** — completing a major platform migration often precedes exit readiness

**Class imbalance handling**: The base rate of transacting is <1% in any given year. Use focal loss or calibrated probability outputs rather than raw classification. SMOTE for synthetic upsampling in training.

### Output

| Field | Description |
|-------|-------------|
| `sell_probability` | Calibrated probability of transaction within 12-24 months |
| `trigger_reasons[]` | Top 3 SHAP-derived reasons (e.g., "founder age 67, no successor identified") |
| `estimated_revenue_range` | Coarse revenue bucket for mandate gating (e.g., "$10-25M") |
| `estimated_ebitda_range` | Coarse EBITDA bucket |
| `signal_freshness` | Age of the most recent signal contributing to the score |

**Gating logic**: Filter out companies outside fund mandate purely on scale (revenue/EBITDA range) and geography. This is coarse gating, not decision-making — valuation precision comes later.

**Scoring cadence**: Full universe re-scored weekly; real-time re-scoring triggered by high-signal events (news, leadership change, advisor engagement).

---

## Stage 2: Thesis Matching & Target Generation

**System**: Thesis-to-Target Generator
**Goal**: Match high-signal companies to specific PE investment theses and rank by fit.

### Thesis Encoding

Each investment thesis is encoded as a structured object:

```yaml
thesis:
  id: "healthcare-it-rollup"
  description: "Acquire and integrate regional healthcare IT services companies"
  sector: ["healthcare IT", "health tech services", "EHR consulting"]
  revenue_range: [5_000_000, 50_000_000]
  ebitda_margin_floor: 0.15
  geography: ["US", "Canada"]
  ownership_preference: ["founder-owned", "family-owned"]
  growth_floor: 0.05
  must_have: ["recurring revenue > 40%", "10+ enterprise clients"]
  nice_to_have: ["SOC 2 certified", "government contracts"]
  anti_patterns: ["single-customer concentration > 30%", "declining revenue"]
```

### Matching Model

**Approach**: Two-stage ranking.

1. **Hard filter**: Apply mandate constraints (revenue range, geography, ownership type, sector). This eliminates ~95% of the universe cheaply.

2. **Semantic similarity + learned ranking**:
   - Encode thesis description and company description (from website, Grata, Crunchbase) using a fine-tuned sentence transformer (e.g., `all-MiniLM-L6-v2` or domain-adapted model)
   - Compute cosine similarity as a baseline relevance score
   - Train a LambdaMART re-ranker on historical deal team accept/reject decisions to learn firm-specific preferences beyond textual similarity
   - Features for re-ranker: semantic similarity, sell probability (from Stage 1), estimated size fit, growth trajectory, margin profile, relationship warmth score

### Valuation Integration (Moderate)

At this stage, attach quick comp-based estimates:
- Pull sector median EV/Revenue and EV/EBITDA multiples from the feature store
- Flag companies that appear cheap relative to sector median ("looks cheap vs sector" flag)
- This is comparative/relative — not yet a credible valuation

### Output

| Field | Description |
|-------|-------------|
| `thesis_id` | Which thesis this target maps to |
| `fit_score` | 0-100 composite fit score |
| `fit_rationale` | LLM-generated 2-3 sentence explanation of why this target fits the thesis |
| `sector_relative_value` | Above/below/at sector median multiple |
| `rank` | Position in the ranked list for this thesis |

**Delivery**: Ranked target lists per thesis, refreshed weekly, with real-time alerts when a high-fit company's sell probability spikes.

---

## Stage 3: Valuation & Alpha Detection

This stage merges the original Shadow Valuation and Alpha Filtering stages. They share model infrastructure and are tightly coupled — you cannot detect mispricing without first estimating value.

### 3A: Shadow Valuation Engine

**Goal**: Attach a credible enterprise value range to every target before any financial disclosure.

**Multi-stage model**:

**Step 1 — Revenue Estimation**:
- Model: XGBoost regressor trained on ~50K+ companies with known revenue (PitchBook, PrivCo, SEC filings)
- Target: Log-transformed annual revenue
- Key features: employee count (strongest single proxy — median ~$130K/employee for SaaS), web traffic volume and trajectory, job posting velocity, app downloads, review volume, sector
- Expected accuracy: ~35% MAPE (significant improvement over 50%+ for manual methods)

**Step 2 — Margin Estimation**:
- Baseline: Sector-median EBITDA margins (Damodaran, NYU Stern — updated annually)
- Adjustments via observable signals:
  - High hiring velocity → margin compression
  - Price increases visible in scraping → pricing power
  - High customer concentration (detectable from review/case study analysis) → margin risk
- Model: Sector-specific LightGBM regressor

**Step 3 — Multiple Assignment**:
- Predict appropriate EV/EBITDA (or EV/Revenue for pre-profit) multiple using:
  - Sector (multiples vary widely: 3-6x for services, 8-15x for software)
  - Estimated growth rate (from employee/traffic trajectory)
  - Profitability tier
  - Current public comp multiples (real-time market conditioning)
  - Deal size (small-cap discount)
- Apply private company illiquidity discount (15-30% below public comp-derived multiples)

**Step 4 — Enterprise Value**:
- `EV = Estimated EBITDA × Predicted Multiple` (or Revenue × Revenue Multiple)
- Confidence intervals via Conformalized Quantile Regression (CQR, using MAPIE library) — guarantees valid coverage without distributional assumptions

**Output**:

| Field | Description |
|-------|-------------|
| `ev_point_estimate` | Central EV estimate |
| `ev_range_80ci` | 80% confidence interval (e.g., "$32M - $62M") |
| `implied_multiple` | EV / Estimated EBITDA |
| `key_value_drivers[]` | Top 3 SHAP-derived drivers of the valuation |
| `confidence_grade` | A/B/C based on data completeness and model certainty |
| `data_freshness` | Age of inputs feeding the estimate |

### 3B: Alpha / Mispricing Detection

**Goal**: Identify non-obvious winners — companies whose fair value significantly exceeds their likely transaction price.

**Model**: For each company, predict "fair" EV/EBITDA multiple using XGBoost trained on completed transactions with known outcomes. Compare to the shadow valuation's implied current multiple.

**Mispricing signals**:

| Signal | What It Detects |
|--------|----------------|
| Revenue growing faster than multiple expanding | Market hasn't priced in growth |
| Employee surge with no funding announcement | Organic growth not yet visible |
| Web traffic inflection point | Product-market fit / viral growth |
| Negative press but strong operational metrics | Temporary narrative discount |
| Sector multiple compression + company margin expansion | Sector-wide discount masking company strength |
| Motivated seller (founder 60+, no succession) | Potential below-market deal |
| Competitor exits/failures | Consolidation opportunity premium |
| Patent/IP filing acceleration | Undervalued technology moat |

**Output**:

| Field | Description |
|-------|-------------|
| `alpha_score` | Expected mispricing magnitude (predicted fair value / shadow value - 1) |
| `mispricing_reason` | LLM-generated narrative explaining the source of alpha |
| `efficiently_priced` | Boolean flag — if true, this is likely auction bait with no edge |

**Pipeline role**: Re-ranks targets by attractiveness × feasibility × alpha. This is the transition from "interesting company" to "potential deal" — and where the firm manufactures edge rather than just processing pipeline.

---

## Stage 4: Outreach & Relationship Orchestration

**System**: AI Outreach Engine
**Goal**: Convert prioritized targets into conversations with founders/owners.

### Relationship Intelligence

Before any outreach, the system maps the firm's existing network to each target:
- **Warmth scoring**: Computed from email exchange frequency/recency, meeting cadence, shared connections (Affinity or 4Degrees integration)
- **Intro path discovery**: Identify the shortest/warmest path from any team member to the target's decision-maker
- **Channel selection**: Warm intro (highest conversion) > event-based outreach > personalized cold email > LinkedIn

### AI-Powered Personalization

**LLM outreach drafting** (Claude or GPT-4 class model):
- **Inputs**: Company signals (recent news, growth trajectory, thesis fit rationale), founder profile (background, published content, conference appearances), relationship context, investment thesis alignment, shadow valuation insights
- **Output**: Personalized email draft that:
  - References a specific, recent signal ("We noticed your expansion into [market]...")
  - Articulates thesis-specific value proposition ("We've built a platform of [X] companies and see [Y] opportunity...")
  - Frames valuation appropriately based on the Founder Price Expectation Model (see below)
  - Maintains the firm's voice and tone (fine-tuned on historical outreach that received positive responses)
- **Human-in-the-loop**: All outreach reviewed by a deal team member before sending. The system produces "90% drafts" — cutting batch outreach time from ~2 hours to 30-60 minutes for 20 emails.

### Founder Price Expectation Model

**Goal**: Predict the founder/owner's likely price expectations to calibrate outreach tone.

**Features**:
- Last known valuation (funding round, insurance valuation, broker listing)
- Sector median multiples at time of most recent comparable exits the founder likely knows about
- Owner's emotional anchoring signals (public statements, prior rejected offers if known)
- Time pressure indicators (approaching retirement, business deterioration, debt maturity)
- Competitive dynamics (other buyers circling)

**Output**:
- `expected_ask_range`: Predicted acceptable price range
- `tone_recommendation`: "premium buyer" (if expectations are high and justified) vs. "disciplined/value buyer" (if expectations exceed fair value)
- `narrative_hooks[]`: Suggested value-creation angles that justify the firm's pricing

### CRM Integration

All outreach activity flows into the CRM (DealCloud, Affinity, or Salesforce) automatically:
- Email open/reply tracking
- Meeting scheduling and notes
- Pipeline stage progression: Sourced → Contacted → Engaged → Meeting → NDA → LOI → Diligence → Close/Pass
- Pass reasons captured for feedback loop

---

## Stage 5: Rapid Underwriting (Pre-LOI)

**System**: Deal Quality / IRR Simulator
**Goal**: Decide whether to pursue aggressively — "Is this worth spending real time and social capital on?"

### Automated LBO Screening

**Inputs** (per deal, auto-populated from upstream stages):

| Parameter | Source | Distribution |
|-----------|--------|-------------|
| Entry EBITDA | Shadow Valuation (Stage 3A) | Normal(μ=estimate, σ=CI width) |
| Entry multiple | Sector median + deal-specific adjustment | Triangular(low, mode, high) |
| Revenue growth rate | Feature store trajectory | Normal(μ=estimated, σ=sector vol) |
| Margin improvement | Thesis-specific value creation plan | Triangular(0%, base, stretch) |
| Debt/equity split | Fund-specific leverage targets | Fixed or Uniform(50%, 70%) |
| Hold period | Fund lifecycle constraints | Discrete(3, 4, 5, 6, 7) |
| Exit multiple | Entry multiple ± expansion/compression | Beta-PERT(bear, base, bull) |

### Monte Carlo Simulation

- 10,000 iterations per deal (numpy-vectorized, runs in seconds)
- Vary each assumption as a distribution, not a point estimate

**Outputs**:

| Field | Description |
|-------|-------------|
| `irr_distribution` | Full distribution with P10, P25, P50, P75, P90 |
| `moic_distribution` | Money-on-invested-capital distribution |
| `p_irr_gt_20` | Probability that IRR exceeds 20% (fund hurdle) |
| `p_irr_gt_25` | Probability that IRR exceeds 25% (high-priority threshold) |
| `downside_irr` | P10 IRR — worst realistic case |
| `key_sensitivities[]` | Top 3 parameters the IRR is most sensitive to |
| `break_even_multiple` | Exit multiple needed to return 1x equity |

### Bid Strategy Integration

Rather than a separate stage, bid strategy is a direct output of the IRR simulator combined with the Founder Price Expectation Model:

| Field | Description |
|-------|-------------|
| `recommended_bid_range` | Entry price range that achieves target IRR |
| `structure_suggestion` | Earnout, rollover equity, or seller note recommendations based on gap between bid and expected ask |
| `win_probability_vs_irr` | Tradeoff curve: higher bid → higher win probability, lower expected IRR |
| `walkaway_price` | Maximum price at which P(IRR > hurdle) drops below acceptable threshold |

### Screening Logic

- **Auto-reject**: P(IRR > 20%) < 30%
- **Priority flag**: P(IRR > 25%) > 40%
- **Rank**: All passing deals ranked by risk-adjusted return (expected IRR / IRR standard deviation)

---

## Stage 6: IC Preparation & Deal Execution Support

**System**: LLM-Powered IC Memo Generator
**Goal**: Accelerate the path from "we want to pursue this" to investment committee decision.

### Automated IC Memo Drafting

**Inputs**: All upstream data — shadow valuation, thesis fit rationale, alpha analysis, IRR simulation, outreach history, any disclosed financials, CIM/teaser if available.

**LLM pipeline** (RAG-based, using Claude or equivalent):
1. Document ingestion: Parse CIM, teasers, data room documents if available (PDF extraction → chunking → vector store)
2. Structured extraction: Pull KPIs (revenue, EBITDA, growth, customer concentration, churn) into standardized fields
3. Memo generation: Produce first-draft IC memo with:
   - Investment thesis and strategic rationale
   - Company overview and competitive positioning
   - Financial summary (disclosed + estimated)
   - Valuation analysis (shadow valuation + comps + IRR scenarios)
   - Key risks and mitigants
   - Value creation plan aligned to thesis
4. Q&A capability: Deal team can ask natural-language questions against all ingested documents

**Output**: Structured IC memo draft (typically cuts research/drafting time by 70-80%), with all claims linked to source data for verification.

**Human-in-the-loop**: IC memos are always reviewed and edited by the deal team. The system produces a first draft, not a final product.

---

## Stage 7: Feedback Loop (The Compounding Moat)

**Goal**: Turn every deal interaction into training data that improves every upstream model.

### Data Captured

| Data Point | Captured When | Used To Retrain |
|------------|---------------|-----------------|
| Outreach outcome (open, reply, meeting, pass) | After each outreach | Outreach personalization, timing models |
| Deal team accept/reject of AI recommendations | At review | Thesis matching re-ranker, signal weights |
| Pipeline progression (NDA → LOI → close/drop) | At each stage gate | Signal detection model, fit scoring |
| Pass reasons (price, fit, quality, timing) | At pass decision | All models — most underused signal |
| Actual cleared price vs. shadow valuation | At close | Shadow valuation model, multiple prediction |
| Actual vs. predicted founder price expectation | At negotiation | Founder expectation model |
| Post-acquisition performance (revenue, EBITDA, IRR) | Quarterly post-close | IRR simulator calibration, value creation assumptions |
| Signal efficacy (which triggers preceded actual transactions) | Continuously | Signal detection feature importance |

### Retraining Protocol

| Model | Retraining Cadence | Minimum New Samples | Drift Detection |
|-------|-------------------|---------------------|-----------------|
| Sell probability | Quarterly | 50 new outcomes | PSI > 0.1 on score distribution |
| Revenue/EBITDA estimator | Quarterly | 100 new disclosed financials | MAPE drift > 5pp |
| Thesis matching re-ranker | Monthly | 20 accept/reject decisions | NDCG drop > 0.05 |
| Shadow valuation | Quarterly | 30 new cleared prices | Calibration drift on CI coverage |
| Founder expectation model | Semi-annually | 15 negotiation outcomes | — |
| Outreach personalization | Monthly | 100 outreach outcomes | Reply rate drop > 2pp |
| IRR simulator | Annually | 10 realized exits | — |

### Drift Detection & Monitoring

- **Population Stability Index (PSI)** on all model score distributions — alert if PSI > 0.1
- **Feature drift**: Monitor input feature distributions for shifts (e.g., sector multiple regime change)
- **Calibration plots**: Monthly check that predicted probabilities match observed frequencies
- **A/B testing**: New model versions shadow-score alongside production models before promotion

---

## Cross-Cutting: Compliance & Governance Layer

This is not a pipeline stage — it's a layer that touches every stage.

### Data Privacy & Legal

| Concern | Mitigation |
|---------|------------|
| **GDPR** (EU targets) | Legitimate interest documentation for each outreach; automated consent tracking; data subject access request handling |
| **CAN-SPAM** (US cold email) | Accurate headers, physical address, working unsubscribe in every email; penalties are $53,088 per violation |
| **Web scraping risk** | Use licensed data providers (Grata, PitchBook, ZoomInfo) over raw scraping; respect robots.txt; no scraping platforms that prohibit it in ToS (LinkedIn) |
| **MNPI controls** | Role-based access controls on deal data; information barriers between portfolio monitoring and sourcing; audit trails on all data access |

### Model Governance

- **Explainability**: SHAP values on every score surfaced to deal teams — models that can't be explained to IC are not adopted
- **Bias auditing**: Quarterly review of model outputs for geographic, sector, or demographic bias
- **Audit trails**: Every model prediction logged with version, features, and timestamp
- **Human-in-the-loop**: No outreach sent, no deal rejected, and no IC memo finalized without human review

---

## Technology Stack Summary

| Layer | Recommended Tools |
|-------|------------------|
| **Data lake/warehouse** | Databricks or Snowflake (medallion architecture) |
| **Orchestration** | Airflow or Dagster |
| **Feature store** | Feast (open source) or Tecton |
| **ML training** | XGBoost, LightGBM, scikit-learn; PyTorch for embeddings |
| **ML serving** | MLflow + custom API layer |
| **LLM backbone** | Claude (Anthropic) for memo generation, outreach drafting, Q&A |
| **Vector database** | pgvector, Pinecone, or Weaviate (for RAG over deal documents) |
| **Entity resolution** | Custom (transformer-based) or Dedupe.io |
| **CRM** | DealCloud, Affinity, or Salesforce (with PE customization) |
| **Relationship intelligence** | Affinity or 4Degrees |
| **Sourcing data** | Grata, PitchBook, Crunchbase, PrivCo |
| **Alternative data** | SimilarWeb, Sensor Tower, Thinknum, G2, Second Measure |
| **Monitoring** | Evidently AI (ML monitoring), Datadog (infra) |

---

## Implementation Phasing

### Phase 1: Foundation (Months 1-3)
- Data ingestion pipelines for Tier 1 sources (PitchBook, Crunchbase, CRM)
- Entity resolution engine (MVP — rule-based blocking + fuzzy matching)
- Feature store with firmographic and ownership features
- Sell probability model v1 (XGBoost on available historical deal data)

### Phase 2: Core Pipeline (Months 4-6)
- Thesis encoding and matching (hard filters + semantic similarity)
- Shadow valuation model v1 (revenue estimation + sector multiples)
- CRM integration (pipeline stage tracking, outreach logging)
- Basic outreach drafting with LLM

### Phase 3: Intelligence Layer (Months 7-9)
- Alternative data integration (web traffic, hiring, reviews)
- Alpha detection model
- Monte Carlo IRR simulator
- Founder price expectation model v1
- IC memo generation (RAG pipeline)

### Phase 4: Optimization & Learning (Months 10-12)
- Feedback loop infrastructure (outcome capture, retraining pipelines)
- Drift detection and monitoring dashboards
- A/B testing framework for model versions
- Entity resolution upgrade (learned similarity model)
- Full relationship intelligence integration

### Phase 5: Compounding (Ongoing)
- Continuous model retraining on deal outcomes
- Expand alternative data sources based on feature importance analysis
- Fine-tune LLM components on firm-specific outreach and memo style
- Cross-portfolio signal development (insights from portfolio companies inform sourcing)

---

## Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Entity resolution errors cascade through pipeline | Duplicate/corrupted scores | Invest heavily in Stage 0B; human review queue for ambiguous matches |
| Stale data scored as current | Misleading recommendations | Track data freshness per feature; decay scores when inputs age |
| Survivorship bias in training | Overfit to companies that transacted | Careful negative sampling; calibrated probabilities; regular calibration checks |
| Deal team ignores AI outputs | Zero ROI on pipeline | SHAP explainability on every score; start with augmentation, not replacement |
| Over-reliance on same data as competitors | No differentiation | Invest in proprietary signals: portfolio company insights, relationship data, event intelligence |
| Model drift in regime changes | Degraded accuracy | PSI monitoring, quarterly retraining, market regime features |
| Compliance violations in automated outreach | Legal/reputational risk | Human review on all outreach; licensed data sources; GDPR/CAN-SPAM compliance built in |

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Proprietary deal flow (% of pipeline from AI sourcing) | >40% by Year 2 | CRM source attribution |
| Outreach-to-meeting conversion rate | >8% (vs. 2-3% industry baseline) | CRM tracking |
| Time from target identification to first outreach | <48 hours | Pipeline timestamps |
| Shadow valuation accuracy (MAPE vs. disclosed) | <40% | Backtested on deals with known financials |
| Signal precision (% of high-score companies that transact within 24mo) | >15% | Outcome tracking |
| IC memo first-draft time | <2 hours (vs. 8-12 hours manual) | Workflow timestamps |
| Model drift detection latency | <1 week | Monitoring system |
| Deals closed from AI-sourced pipeline | ≥2 per fund per year | Deal attribution |
