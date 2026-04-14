# Feast (Feature Store)

## What It Is

Feast is a free, open-source feature store. It manages the lifecycle of ML features: defining them, computing them, storing them with timestamps, and serving them consistently to both training and inference. It ensures that the exact same feature logic and values are used everywhere, eliminating a common source of silent model degradation called training/serving skew.

## Why You Might Want It

The pipeline currently computes features inline -- each stage builds its feature vectors on the fly and passes them directly to models. This works fine for a batch pipeline, but breaks down in three scenarios:

1. **Historical training data.** When you train the sell probability model, you need features as they existed *before* each transaction happened. If Company X was acquired on June 15, 2024, you need its employee growth and revenue trajectory as of, say, March 2024 -- not today's values. Without time-travel queries, you'll accidentally train on future data (lookahead bias) and your model will look great in backtesting but fail in production.

2. **Feature reuse across models.** The `employee_growth_90d` feature is used by both the signal detection model and the margin estimator. Today each computes it independently. If one implementation drifts slightly from the other, both models degrade in ways that are hard to debug.

3. **Incremental scoring.** If you want to rescore a single company when new data arrives (rather than re-running the entire pipeline), you need a way to fetch that company's current features instantly. A feature store pre-materializes these for sub-millisecond reads.

## When to Add It

Add Feast when:
- You have trained models that need historical feature snapshots for retraining
- You're computing the same features in multiple places and want a single source of truth
- You want to serve features in real-time for event-triggered scoring

You do NOT need it when:
- All your models run in a single batch pipeline (current state)
- You have fewer than ~20 features
- You're the only developer

## How to Add It to This Pipeline

### 1. Install

```bash
pip install "deal-sourcing[feature-store]"  # feast is already in pyproject.toml
```

### 2. Create Feature Definitions

The empty `src/feature_store/definitions/` directory is where these go. Create a file like:

```python
# src/feature_store/definitions/company_features.py
from datetime import timedelta
from feast import Entity, FeatureView, Field, FileSource
from feast.types import Float64, Int64, String

company = Entity(name="company", join_keys=["entity_id"])

company_source = FileSource(
    path="data/company_features.parquet",  # your feature table
    timestamp_field="feature_timestamp",
)

company_signals = FeatureView(
    name="company_signals",
    entities=[company],
    schema=[
        Field(name="employee_growth_30d", dtype=Float64),
        Field(name="employee_growth_90d", dtype=Float64),
        Field(name="revenue_growth_yoy", dtype=Float64),
        Field(name="estimated_revenue", dtype=Float64),
        Field(name="estimated_ebitda_margin", dtype=Float64),
        Field(name="founder_age", dtype=Float64),
        Field(name="owner_tenure_years", dtype=Float64),
        Field(name="sector_ma_activity_12m", dtype=Float64),
    ],
    source=company_source,
    ttl=timedelta(days=90),
)
```

### 3. Create Feature Transforms

The empty `src/feature_store/transforms/` directory is for the computation logic. Move your inline feature computation here so it's reusable:

```python
# src/feature_store/transforms/company_transforms.py
def compute_company_features(raw_data: pd.DataFrame) -> pd.DataFrame:
    """Compute all company-level features from raw ingested data."""
    features = pd.DataFrame()
    features["entity_id"] = raw_data["entity_id"]
    features["feature_timestamp"] = raw_data["updated_at"]
    features["employee_growth_90d"] = ...  # your logic
    features["revenue_growth_yoy"] = ...
    return features
```

### 4. Apply and Materialize

```bash
cd src/feature_store
feast apply                                    # register feature definitions
feast materialize 2023-01-01 2025-12-31        # backfill historical features
```

### 5. Use in the Pipeline

Replace inline feature computation with Feast lookups:

```python
from feast import FeatureStore

store = FeatureStore(repo_path="src/feature_store")

# Training: point-in-time join (time-travel)
training_df = store.get_historical_features(
    entity_df=labeled_companies_df,  # entity_id + event_timestamp
    features=[
        "company_signals:employee_growth_90d",
        "company_signals:revenue_growth_yoy",
        "company_signals:estimated_ebitda_margin",
    ],
).to_df()

# Inference: latest features for a single company
features = store.get_online_features(
    features=["company_signals:employee_growth_90d"],
    entity_rows=[{"entity_id": "abc-123"}],
).to_dict()
```

### Storage Backend

For the POC, Feast uses SQLite locally (zero configuration). For production, you'd point it at Postgres (which is already in docker-compose):

```yaml
# src/feature_store/feature_store.yaml
project: deal_sourcing
registry: data/registry.db
provider: local
online_store:
  type: sqlite
  path: data/online_store.db
```

## Cost

Free. Feast is Apache 2.0 licensed. The commercial version (Tecton) charges for managed infrastructure, but the open-source version has the same core functionality.
