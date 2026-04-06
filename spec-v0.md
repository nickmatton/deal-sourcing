# AI/ML-Powered Private Equity Deal Sourcing Pipeline
## Technical Specification v1.0

---

## Executive Summary

This document specifies a fully integrated, AI/ML-powered deal sourcing pipeline for a private equity firm. The pipeline spans from raw data ingestion through bid execution and closes the loop with a feedback system that compounds the firm's informational edge over time.

The architecture described here refines the original 9-stage design into **8 stages** by merging coarse valuation gating into the deal signal stage where it naturally belongs, and by elevating two cross-cutting concerns — **Compliance & Governance** and **Relationship Intelligence** — into first-class architectural layers rather than burying them inside individual stages.

**Key changes from the original design:**

- **Removed:** Stage 2's separate "quick comp-based multiple estimates" — this duplicated work that belongs in the Shadow Valuation Engine. Thesis matching should score fit, not price.
- **Added:** A dedicated Compliance & Governance layer (data privacy, regulatory, audit trail) that spans the entire pipeline.
- **Added:** A Relationship Intelligence Graph as a persistent, cross-cutting data structure rather than treating relationships as a feature of outreach alone.
- **Restructured:** The Outreach Engine now consumes relationship intelligence as a first-class input rather than generating it ad hoc.
- **Elevated:** The Feedback Loop from a conceptual stage into a concrete MLOps system with defined retraining triggers, data contracts, and drift detection.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                  CROSS-CUTTING LAYERS                               │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Compliance & Governance Layer (GDPR, CCPA, audit trails)    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Relationship Intelligence Graph (firm-wide network map)     │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Stage 0 │───▶│  Stage 1 │───▶│  Stage 2 │───▶│  Stage 3 │
│  Data    │    │  Signal  │    │  Thesis  │    │  Shadow  │
│  Ingest  │    │  Detect  │    │  Match   │    │  Value   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                      │
┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  Stage 7 │◀───│  Stage 6 │◀───│  Stage 5 │◀───┌──────────┐
│  Feedback│    │  Bid     │    │  Rapid   │    │  Stage 4 │
│  Loop    │    │  Strategy│    │  UW      │    │  Alpha + │
└──────────┘    └──────────┘    └──────────┘    │  Outreach│
     │                                          └──────────┘
     └──────── retrains all models ──────────────────────▶
```

---

## Cross-Cutting Layer A: Compliance & Governance

**Why this exists as its own layer:** Every stage touches personal data (executive names, compensation proxies, contact details, behavioral signals). GDPR, CCPA, and the EU AI Act impose obligations that cannot be bolted on after the fact. Regulatory penalties for PE firms can be levied at the "undertaking" level, meaning fines can apply across the fund and portfolio companies as a single economic unit.

### Requirements

**Data Provenance & Lineage**
- Every data point entering the system must carry metadata: source, collection timestamp, legal basis for processing, and retention expiry.
- A lineage graph must allow any output (a score, a target ranking, a valuation estimate) to be traced back to its input data and the model version that produced it.

**Consent & Legal Basis Management**
- Personal data (names, emails, behavioral signals tied to identifiable individuals) must be tagged with legal basis: legitimate interest, consent, or contractual necessity.
- Automated DSAR (Data Subject Access Request) handling: if a target company's founder requests data access or deletion, the system must be able to surface and purge all records within the regulatory timeframe.
- Data minimization enforcement: the system should only retain personal data necessary for the stated purpose and auto-expire records based on configurable retention policies.

**AI Act Compliance**
- Any model that influences outreach to, or valuation of, EU-connected entities must maintain a technical documentation file per EU AI Act requirements.
- High-risk classification assessment for models that score individuals (founder behavior models, executive churn models).
- Human-in-the-loop checkpoints at Stages 4 and 6 where AI outputs directly influence real-world actions (outreach, bid submission).

**Audit Trail**
- Immutable log of all model predictions, human overrides, and deal progression decisions.
- Queryable by deal, by model, by time window.
- Retained for the life of the fund plus regulatory tail (typically 7–10 years).

---

## Cross-Cutting Layer B: Relationship Intelligence Graph

**Why this is elevated from Stage 5 to a cross-cutting layer:** Relationships are not just an outreach input — they are a strategic asset that informs every stage. Knowing that your Operating Partner sat on a board with the target's CEO changes signal detection (you may get information earlier), thesis matching (you can execute complex strategies with trusted management), valuation (you may get a bilateral deal at a better price), and bid strategy (you know the seller's preferences).

### Data Model

The Relationship Intelligence Graph is a persistent, continuously-updated knowledge graph with the following node types:

- **People:** Partners, associates, operating partners, advisors, portfolio company executives, target company executives, intermediaries, bankers.
- **Organizations:** Fund entities, portfolio companies, target companies, banks, law firms, accounting firms.
- **Events:** Meetings, calls, emails, conference co-attendance, co-investments, board seats, prior transactions.

### Edge Properties

Every relationship edge carries:
- **Strength score** (0–1): Derived from recency, frequency, and depth of interaction. Decays over time if not reinforced.
- **Path type:** Direct, one-hop introduction, two-hop introduction.
- **Last interaction:** Timestamp of most recent meaningful contact.
- **Context tags:** Co-investor, co-board-member, former-colleague, conference-contact, banker-introduced.

### Ingestion

- Automated capture from email metadata (not content — compliance layer enforces this), calendar events, CRM entries, LinkedIn connection data (with appropriate consent), and conference attendee lists.
- Manual enrichment by deal team (flagging warm relationships, noting meeting outcomes).
- Portfolio company org charts and board compositions.

### Outputs Consumed By

- **Stage 1 (Signal Detection):** Relationship proximity to a company increases the weight of weak signals.
- **Stage 2 (Thesis Matching):** Fit scores incorporate "can we actually reach this company."
- **Stage 4 (Outreach):** Warm path routing — who in the firm should make the first call, and through whom.
- **Stage 6 (Bid Strategy):** Relationship depth influences whether a bilateral approach is feasible vs. requiring a process.

---

## Stage 0: Data Ingestion & Feature Store

### Purpose

Build and maintain a unified, time-series-aware, company-level feature store that serves all downstream models. This is the foundation — garbage in, garbage out.

### Data Streams

**Structured Company Data**
- Sources: PitchBook, Crunchbase, Capital IQ, D&B, government registries (Companies House, SEC EDGAR, state filings).
- Entities: Company name, industry codes (NAICS/SIC/TRBC), founding date, HQ location, ownership structure, funding history, known financials.
- Refresh cadence: Daily for public filings, weekly for commercial databases, real-time for API-connected sources.

**Talent & Organizational Signals**
- Sources: LinkedIn (via official API with appropriate data use agreements), Glassdoor, job boards (Indeed, Greenhouse, Lever public postings).
- Signals: Headcount growth/decline, executive turnover (C-suite, VP+), department-level hiring velocity, Glassdoor sentiment trends, key person departures.
- Refresh cadence: Weekly scrape/API pull. Executive changes flagged in near-real-time via news monitors.

**Financial Proxies (for private companies)**
- Sources: SimilarWeb/Semrush (web traffic), Sensor Tower/data.ai (app downloads/usage), G2/Capterra (B2B software reviews), credit agencies, import/export data, patent filings.
- Signals: Revenue proxies (traffic-to-revenue models by sector), growth trajectory, product-market fit indicators, competitive positioning.
- Refresh cadence: Monthly for traffic/app data, quarterly for review aggregations.

**Transaction & Market Data**
- Sources: PitchBook, Dealogic, Refinitiv, proprietary deal logs, public M&A filings.
- Entities: Historical deal multiples (EV/EBITDA, EV/Revenue), deal structure (LBO, growth, add-on), buyer/seller profiles, sector comps.
- Refresh cadence: As transactions are reported. Historical data is static but must be version-controlled.

**Alternative Data**
- Sources: News (via NLP pipeline), regulatory filings, patent databases, government contract awards, real estate records, corporate litigation dockets.
- Signals: Sentiment shifts, regulatory risk, IP portfolio changes, legal exposure.
- Refresh cadence: Daily for news. Weekly for filings. Real-time alerting for material events.

### Feature Store Architecture

- **Storage:** Time-series columnar store (e.g., Apache Iceberg on cloud object storage) for historical features. A low-latency serving layer (e.g., Redis/Feast) for real-time model inference.
- **Schema:** Each company record is keyed by a canonical entity ID (resolved via entity resolution — see below). Features are versioned and timestamped. No feature is stored without source attribution.
- **Entity Resolution:** Critical. The same company appears differently across data sources. The system must maintain a probabilistic entity resolution model that merges records across sources using name, domain, address, executive overlap, and other signals. False merge rate must be monitored and kept below 0.1%.
- **Graph Layer:** Company-to-company relationships (customer/supplier, investor/investee, competitor) are maintained as a separate graph overlay on the feature store, enabling graph-based model features.

### Key Technical Decisions

- All raw data is retained in a bronze/silver/gold medallion architecture. Models consume from gold (cleaned, resolved, featurized). Analysts can query silver (cleaned but not featurized) for ad hoc research.
- Data quality monitors run continuously: completeness checks, freshness checks, distribution drift alerts. A data source that degrades triggers alerts before downstream models are affected.

---

## Stage 1: Deal Signal Detection

### Purpose

Identify companies that are likely to transact (sell, seek investment, or be open to a conversation) within a 6–24 month window. This is top-of-funnel filtering — the goal is recall over precision, with rough size gating to avoid wasting cycles on companies outside the fund's mandate.

### System: Latent Deal Signal Engine

**Model Architecture**
- An ensemble model combining:
  - **Temporal event model** (LSTM or Transformer over company event sequences): Detects patterns in the temporal ordering of events that historically precede transactions. Example: "CFO departure → hiring freeze → advisor engagement → sale process."
  - **Gradient-boosted classifier** (XGBoost/LightGBM) over static + rolling features: Ownership age, founder age, revenue growth inflection, debt maturity, sector M&A cycle timing.
  - **Graph neural network** on the company relationship graph: Detects contagion effects (a PE firm acquiring one company in a sector increases transaction probability for peers) and supply chain stress.

**Output Schema**
- `company_id`: Canonical entity ID
- `sell_probability`: Float [0, 1] — probability of a transaction event in the next 6/12/24 months (three horizons)
- `trigger_reasons`: List of tagged drivers, each with a confidence weight:
  - `succession_risk` — Founder age > 60, no identified successor, key person insurance changes
  - `growth_inflection` — Revenue proxy acceleration suggesting the company is entering a sellable window
  - `financial_distress` — Credit deterioration, covenant triggers, debt maturity wall
  - `strategic_review` — Advisor engagement, board composition changes, "strategic alternatives" language in press
  - `sector_wave` — Elevated M&A activity in the company's sub-sector
  - `ownership_fatigue` — PE-backed company past typical hold period, sponsor fund approaching end of life
  - `regulatory_catalyst` — New regulation creating consolidation pressure or exit motivation
- `estimated_scale`: Coarse revenue/EBITDA band (e.g., "$20–50M revenue, $5–12M EBITDA") derived from financial proxy models. This is NOT a valuation — it is a size gate.
- `mandate_pass`: Boolean — does the estimated scale fall within at least one active fund mandate? Companies that fail this gate are deprioritized but not deleted (mandates change).

**Coarse Size Gating (moved from original Stage 2)**
- The original pipeline had "quick comp-based multiple estimates" in Stage 2 (Thesis Matching). This is removed. Thesis matching should evaluate strategic fit, not price. Rough size estimation belongs here in signal detection as a practical filter — there is no point matching a $500M revenue company to a lower-middle-market fund's thesis.
- Size estimation uses a sector-specific revenue proxy model (e.g., web traffic × sector-specific revenue-per-visit coefficient, validated against known financials for similar companies).

### Retraining Triggers

- Quarterly retraining on new confirmed transaction data.
- Immediate retraining if precision at the 50th percentile threshold drops below the defined SLA (monitored via the feedback loop).

---

## Stage 2: Thesis Matching & Target Generation

### Purpose

Match signal-positive companies to specific, codified investment theses. Every PE fund operates with defined theses (e.g., "buy-and-build platform in fragmented specialty distribution," "technology-enabled services with >80% recurring revenue," "founder-led businesses with succession opportunity in industrials"). This stage scores how well each opportunity fits each active thesis.

### System: Thesis-to-Target Generator

**Thesis Encoding**
- Each investment thesis is formally encoded as a structured object:
  - `sector_filters`: NAICS/TRBC codes, keyword clusters
  - `financial_profile`: Revenue range, margin profile, growth rate, recurring revenue %, capex intensity
  - `strategic_attributes`: Market position (leader/challenger/niche), customer concentration, geographic footprint, regulatory moat, technology differentiation
  - `deal_type`: Platform vs. add-on, majority vs. minority, growth vs. buyout
  - `value_creation_levers`: Operational improvement potential, buy-and-build, pricing power, geographic expansion, digital transformation
  - `exclusions`: Hard no-go criteria (e.g., no exposure to fossil fuels, no sub-scale businesses)
- Theses are maintained by investment professionals and versioned. The system can track how theses evolve and whether historical thesis changes improved or degraded deal quality.

**Matching Model**
- A two-stage model:
  1. **Hard filter:** Deterministic rules that eliminate companies that violate thesis exclusions or are outside scope on sector, geography, or deal type.
  2. **Soft scoring:** A learned model (fine-tuned LLM for semantic matching + gradient-boosted model for quantitative features) that produces a fit score. The LLM component handles fuzzy matching — e.g., a thesis targeting "technology-enabled services" should match a company described as "SaaS-powered field service management" even if the NAICS code says "facilities management."

**Output Schema**
- `thesis_id` + `company_id` pair
- `fit_score`: Float [0, 1]
- `fit_rationale`: Natural language explanation of why this company fits (or partially fits) the thesis. Generated by the LLM component and reviewed by humans for the top-ranked targets.
- `gap_flags`: Specific areas where the company diverges from the thesis (e.g., "revenue below thesis minimum but growing at 40% YoY," "customer concentration at 35% — thesis ceiling is 25%").
- `reachability_score`: Derived from the Relationship Intelligence Graph — how many hops to a warm introduction, and through whom.

**What was removed from this stage vs. the original design:** The original Stage 2 included "quick comp-based multiple estimates" and "'looks cheap vs sector' flags." This is premature. At the thesis matching stage, you do not yet have enough context to make even rough valuation calls — and doing so risks anchoring the team on bad numbers before proper analysis. Valuation belongs entirely in Stage 3.

---

## Stage 3: Shadow Valuation Engine

### Purpose

Attach a credible enterprise value range to every company that passes thesis matching. This is the pivotal stage that converts "interesting company" into "potential deal." The shadow valuation is not a final bid price — it is a range estimate with a confidence interval, used to:
1. Re-rank targets by attractiveness (fit + value).
2. Filter by feasibility (can the fund afford this? Does it fit the check size?).
3. Feed downstream stages with a price anchor.

### System: Shadow Valuation Engine

**Model Architecture: Multi-Method Ensemble**

The valuation engine does not rely on a single approach. It runs three independent valuation methods and synthesizes them:

1. **Comparable Transaction Model**
   - Retrieves the N most similar historical transactions from the transaction database, using a learned similarity metric (sector, size, growth profile, margin, geography, deal type).
   - Extracts implied EV/EBITDA and EV/Revenue multiples from these comps.
   - Applies the median (and range) of comp multiples to the target's estimated financials.
   - Adjusts for size premium/discount, growth premium, margin quality, and current market conditions (interest rate environment, credit availability).

2. **Regression-Based Multiple Predictor**
   - A gradient-boosted regression model (LightGBM) trained on historical transactions. Features include: sector, sub-sector, revenue, EBITDA margin, revenue growth rate, customer concentration, recurring revenue %, geographic mix, buyer type (PE vs. strategic), market conditions at time of deal.
   - Output: Predicted EV/EBITDA multiple with prediction interval.
   - This model captures non-linear relationships that simple comp screening misses (e.g., the interaction between growth rate and margin in determining the multiple).

3. **Proxy DCF Model**
   - For companies where enough financial proxy data exists, a simplified discounted cash flow model is run.
   - Revenue trajectory extrapolated from proxy data. Margin assumptions from sector benchmarks. Terminal value via Gordon growth model at sector-appropriate rates.
   - This catches cases where comp-based methods are misleading (e.g., a company with a dramatically different growth profile than available comps).

**Synthesis Layer**
- A meta-model combines the three estimates, weighting them by confidence (which depends on data availability — comp-based gets higher weight when good comps exist; DCF gets higher weight when financial proxies are rich).
- Output is a **distribution**, not a point estimate: P10 / P25 / P50 / P75 / P90 of enterprise value.

**Output Schema**
- `company_id`
- `ev_range`: {p10, p25, p50, p75, p90} in dollars
- `implied_multiple`: {ev_ebitda, ev_revenue} at P50
- `confidence`: {low, medium, high} — driven by data completeness and comp quality
- `key_drivers`: The 3–5 features most influential in determining the valuation (via SHAP values from the regression model). Example: "Valuation driven primarily by: (1) 30%+ revenue growth, (2) 85% recurring revenue, (3) elevated sector multiples in Q1 2026."
- `comp_set`: The specific transactions used as comparables, with links to source data.
- `feasibility_flags`: Does this fall within fund check size? Does it require co-investment? Does the EV imply leverage levels within the fund's appetite?

---

## Stage 4: Alpha Filtering + Outreach Orchestration

**Why these two stages are merged:** The original pipeline had Alpha Filtering (Stage 4) and Outreach (Stage 5) as separate stages. In practice, mispricing detection and outreach prioritization are inseparable — you detect alpha *in order to* prioritize outreach. Separating them creates an unnecessary handoff. The merged stage detects mispricing, prioritizes the outreach queue, and orchestrates the actual outreach motion.

### Part A: Mispricing Detection

**System: Alpha Scoring Engine**

**Purpose:** Identify companies where the shadow valuation suggests the market (or the seller) is likely to misprice the asset — either because the market doesn't see what the fund sees, or because the deal can be sourced bilaterally at a discount to a competitive process.

**Model Architecture**
- Takes as input: Shadow valuation, thesis fit score, and a set of "alpha features" not captured in standard comp analysis:
  - **Operational improvement potential:** Benchmarked against portfolio company performance data. "This company's SG&A is 8pp above peer median — our operating playbook could close 5pp of that."
  - **Buy-and-build premium:** If the fund already owns a platform in this sector, the add-on acquisition value exceeds standalone value.
  - **Market timing:** Sector is currently out of favor (depressed multiples) but fundamentals are inflecting.
  - **Information asymmetry:** The company has characteristics the broader market undervalues (e.g., embedded software revenue classified as "industrial" by databases, recurring revenue hidden in long-term contracts).
  - **Process avoidance probability:** How likely is it that this deal can be done bilaterally? (Higher alpha if bilateral — avoids auction premium.)

**Output Schema**
- `company_id`
- `alpha_score`: Float [0, 1] — composite mispricing opportunity
- `alpha_drivers`: Tagged reasons (operational_improvement, buy_and_build, market_timing, information_asymmetry, bilateral_probability)
- `outreach_priority`: Ordinal rank combining fit score, alpha score, and time sensitivity (if the signal suggests the company may run a process soon, priority increases)
- `efficiently_priced_flag`: Boolean — if the company is likely to trade at fair value in a competitive auction and the fund has no differentiated angle, this flag recommends deprioritizing. This is the "auction bait" filter.

### Part B: Outreach Orchestration

**System: AI Outreach Engine**

**Purpose:** Convert prioritized targets into conversations. This is where the system shifts from analysis to action.

**Inputs**
- Outreach priority queue from Alpha Scoring
- Relationship Intelligence Graph (warm paths)
- Shadow valuation (for price framing)
- Thesis fit rationale (for narrative construction)

**Founder/Owner Price Expectation Model**
- A model trained on historical data of seller price expectations vs. actual deal prices.
- Features: Company age, founder age, recent funding rounds (and at what valuation), sector heat, company's own marketing language (confident vs. cautious), comparable recent exits that the founder likely knows about.
- Output: Predicted seller price expectation range and seller sophistication score.
- Used to calibrate outreach tone: If expected seller price is well above shadow valuation, the outreach emphasizes "long-term partnership" and "growth acceleration" (avoids price discussion). If the seller is likely realistic, the outreach can be more direct.

**Outreach Generation**
- Personalized outreach narratives generated by LLM, drawing on:
  - The specific thesis fit rationale ("We see untapped value in your recurring revenue base that the market prices as project revenue")
  - The warm path from the Relationship Intelligence Graph ("Our Operating Partner, [Name], served on the [Industry Association] board with your CFO")
  - Tone calibration from the Price Expectation Model
- All outreach is human-reviewed before sending. The system drafts; humans approve and edit.
- Multi-channel sequencing: Initial outreach, follow-up timing, channel selection (email, phone, in-person at upcoming conference), escalation paths.

**Outreach Tracking**
- Every outreach attempt is logged: channel, content summary (not full text — compliance), timestamp, response (yes/no/not-yet), next action.
- This data feeds the Feedback Loop for model retraining.

---

## Stage 5: Rapid Underwriting (Pre-LOI)

### Purpose

Before committing significant time and relationship capital, determine whether a target can meet the fund's return threshold. This stage answers: "Is this worth pursuing aggressively?"

### System: Deal Quality / IRR Simulator

**Inputs**
- Shadow valuation (entry price estimate)
- Thesis and value creation plan
- Financing assumptions (leverage levels, interest rates, terms — sourced from current market conditions)
- Operating model assumptions (revenue growth, margin expansion, capex requirements)
- Exit assumptions (hold period, exit multiple — derived from historical sector exits and current market trends)

**Simulation Engine**
- Monte Carlo simulation over key uncertain variables:
  - Entry price (sampled from shadow valuation distribution)
  - Revenue growth trajectory (sampled from a range informed by financial proxies and sector data)
  - Margin evolution (sampled from a range informed by operational benchmarking)
  - Exit multiple (sampled from historical distribution for sector, adjusted for current market cycle)
  - Financing terms (sampled from current market conditions with stress scenarios)
- Each simulation run produces: Gross IRR, MOIC (Multiple on Invested Capital), cash-on-cash returns.
- 10,000+ simulations per target.

**Output Schema**
- `company_id`
- `irr_distribution`: {p10, p25, p50, p75, p90}
- `moic_distribution`: {p10, p25, p50, p75, p90}
- `downside_risk`: Probability of IRR < cost of capital. Probability of MOIC < 1.0x (loss scenario).
- `key_sensitivities`: Tornado chart data — which input assumptions most move the IRR? (Typically: entry multiple, revenue growth, exit multiple.)
- `go_no_go_recommendation`: Based on configurable hurdle rates. "Strong Pursue" / "Pursue with Caution" / "Pass" — with reasons.
- `value_creation_bridge`: Waterfall decomposition of where return comes from: revenue growth, margin expansion, multiple expansion, deleveraging, add-on acquisitions.

### Human Checkpoint

This stage requires human review before any outreach escalation or LOI preparation. The simulation provides the quantitative frame; the deal team provides judgment on qualitative factors (management quality, market dynamics, competitive landscape) that the models cannot fully capture.

---

## Stage 6: Bid Strategy Optimization

### Purpose

For deals that pass rapid underwriting and have progressed to active negotiation, determine what to bid and how to structure the offer to maximize win probability at an acceptable return.

### Systems Combined

- Shadow Valuation Engine (refreshed with any new information gathered during diligence)
- Founder/Owner Price Expectation Model (updated with signals from actual conversations)
- IRR Simulator (re-run with refined assumptions)

### Bid Optimization Model

**Inputs**
- Updated shadow valuation range
- Updated seller price expectation
- IRR hurdle rate (firm policy)
- Competitive dynamics (are other bidders involved? How many? What are their likely profiles?)
- Structural preferences (does the seller want all-cash? Is rollover equity attractive? Are earnouts acceptable?)

**Optimization Objective**
- Maximize: `P(win) × E[IRR | win]`
- Subject to: IRR ≥ hurdle at P50, MOIC ≥ minimum at P25, downside probability ≤ threshold

**Output Schema**
- `recommended_bid_price`: Point estimate + range
- `recommended_structure`: {cash_pct, debt_pct, rollover_pct, earnout_terms}
- `win_probability`: Estimated from seller expectation model and competitive dynamics
- `irr_at_bid`: {p25, p50, p75} if the bid is accepted
- `alternative_scenarios`: 2–3 alternative bid/structure combinations with different risk/return/win-probability tradeoffs. Presented to the deal team as options, not a single recommendation.
- `negotiation_guide`: Key points for the deal team — where is the seller likely flexible? What structural features might bridge a price gap?

---

## Stage 7: Feedback Loop & MLOps

### Purpose

This is the system's compounding edge. Every deal outcome — whether it results in a closed transaction, a passed opportunity, or a failed outreach — generates training signal. The feedback loop ensures every model in the pipeline improves over time.

**This is not a conceptual aspiration. It is a concrete MLOps system with defined contracts.**

### Data Capture

Every stage emits structured outcome data:

| Stage | Data Captured | Latency |
|-------|--------------|---------|
| Signal Detection | Did the company actually transact within the predicted window? | 6–24 months |
| Thesis Matching | Did the deal team agree with the fit score? (Human label) | Days |
| Shadow Valuation | What was the actual transaction price vs. predicted range? | Months |
| Alpha Filtering | Did "high alpha" targets actually close at better terms than "low alpha" targets? | Months–Years |
| Outreach | Response rate by channel, by relationship path, by narrative type | Days–Weeks |
| Rapid Underwriting | Did the deal pass IC? Did actual returns match simulated IRR? | Months–Years |
| Bid Strategy | Did the bid win? At what price vs. recommended? | Weeks–Months |

### Model Retraining Protocol

- **Scheduled retraining:** All models retrained quarterly on new data.
- **Triggered retraining:** If model performance metrics (monitored in real-time via a model monitoring dashboard) degrade past a threshold, retraining is triggered immediately. Metrics monitored:
  - Signal Detection: Precision/recall at operating threshold
  - Shadow Valuation: Mean absolute percentage error (MAPE) of P50 vs. actual price
  - Outreach: Response rate vs. predicted response rate
  - Bid Strategy: Win rate vs. predicted win probability
- **Champion/challenger deployment:** New model versions are deployed as challengers alongside the existing champion. A fraction of traffic is routed to the challenger. If the challenger outperforms over a defined evaluation window, it is promoted.
- **Concept drift detection:** Statistical tests (PSI, KS test) run on feature distributions and prediction distributions to detect when the world has shifted beneath the model.

### Long-Term Return Tracking

The highest-value feedback signal — actual investment returns — takes 3–7 years to materialize. The system maintains a mapping from initial pipeline scores to eventual fund-level returns. After sufficient data accumulates (typically after the second fund cycle), this enables:

- Identifying which signal combinations predict top-quartile returns (not just deal completion).
- Adjusting the alpha model to optimize for IRR, not just deal volume.
- Quantifying the actual dollar value of the AI pipeline's contribution to fund performance.

---

## Data & Infrastructure Requirements

### Compute

- Model training: GPU-enabled instances for LLM fine-tuning and GNN training. Standard CPU for gradient-boosted models.
- Inference: Low-latency serving for real-time scoring (signal detection, thesis matching) when new data arrives. Batch scoring acceptable for valuation and underwriting.
- Simulation: Parallel compute for Monte Carlo (Stage 5).

### Storage

- Feature store: Cloud object storage (S3/GCS) with Apache Iceberg for versioned, time-travel-capable tables.
- Graph database: Neo4j or Amazon Neptune for the Relationship Intelligence Graph.
- Vector store: For LLM-powered semantic search over company descriptions, thesis documents, and deal memos.
- Audit log: Append-only, encrypted, retained for fund life + 10 years.

### External Data Budget

- Commercial data subscriptions (PitchBook, Capital IQ, SimilarWeb, etc.) represent the largest ongoing cost. Budget $500K–$2M/year depending on fund size and coverage needs.
- Alternative data (web scraping, NLP pipelines) requires dedicated engineering to maintain.

### Team

- **ML Engineers** (2–3): Model development, training, deployment, monitoring.
- **Data Engineers** (2–3): Ingestion pipelines, feature store, data quality.
- **Product/Platform Engineer** (1–2): UI for deal team interaction with the pipeline, dashboards, outreach tools.
- **Compliance/Data Privacy** (1): Ongoing governance, DSAR handling, regulatory monitoring.
- **Deal Team Integration:** The system is only as good as its adoption. A dedicated internal champion (likely a senior associate or VP) who bridges the deal team and the engineering team.

---

## Implementation Roadmap

### Phase 1 (Months 0–4): Foundation
- Deploy feature store with 3–5 core data sources.
- Build entity resolution system.
- Implement compliance layer (data lineage, consent management, audit trail).
- Build initial signal detection model using historical transaction data.
- Ship basic thesis encoding and hard-filter matching.

### Phase 2 (Months 4–8): Core Intelligence
- Deploy Shadow Valuation Engine (comp-based model first, then regression, then proxy DCF).
- Build Relationship Intelligence Graph from CRM and email metadata.
- Launch thesis soft-scoring with LLM component.
- Build outreach tracking infrastructure.
- First version of alpha scoring.

### Phase 3 (Months 8–14): Orchestration & Optimization
- Deploy Outreach Orchestration (narrative generation, multi-channel sequencing).
- Build IRR Simulator and rapid underwriting workflow.
- Build Bid Strategy Optimizer.
- Deploy model monitoring and retraining pipeline.
- Integrate everything into a deal team–facing dashboard.

### Phase 4 (Months 14+): Compounding
- Close the feedback loop with real outcome data.
- Retrain all models on proprietary deal outcome data.
- Build long-term return tracking.
- Expand data sources based on what the models find most predictive.
- Iterate on thesis encoding as investment strategy evolves.

---

## Success Metrics

| Metric | Baseline (Manual) | Target (AI Pipeline) |
|--------|-------------------|---------------------|
| Proprietary deal flow % | 15–25% | 40–55% |
| Time from signal to first outreach | 4–8 weeks | 3–7 days |
| Outreach response rate | 8–12% | 18–30% |
| Shadow valuation accuracy (MAPE) | N/A (no systematic tracking) | <30% vs. actual |
| Deals reviewed per associate per quarter | 30–50 | 150–250 (with AI pre-screening) |
| IC conversion rate (deals reviewed → LOI) | 3–5% | 8–15% |
| Bid win rate (when we bid) | 20–30% | 35–50% |

---

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Data vendor lock-in | Medium | Abstract data sources behind a common schema. Multi-vendor strategy for critical data. |
| Model overfit to historical deal environment | High | Ensemble methods, concept drift monitoring, human override capability at every stage. |
| GDPR/CCPA enforcement action | High | Compliance layer is a first-class citizen, not an afterthought. DPIA for all personal data processing. |
| Deal team non-adoption | High | Co-design with deal team from Day 1. The system must save time, not create it. Dedicated internal champion. |
| Adversarial data (companies gaming signals) | Low | Monitor for anomalous signal patterns. Cross-validate signals across independent data sources. |
| Over-reliance on AI recommendations | Medium | Mandatory human-in-the-loop at Stages 4, 5, and 6. System provides options, not decisions. |

---

*This specification is a living document. It should be updated as the pipeline is built, as models are trained, and as the deal team provides feedback on what is and isn't working. The feedback loop applies to the spec itself, not just the models.*