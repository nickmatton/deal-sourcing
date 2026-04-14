# MLflow (Experiment Tracking & Model Registry)

## What It Is

MLflow is a free, open-source platform for managing the ML lifecycle. In practice, most people use two of its four components:

1. **Experiment Tracking** -- log hyperparameters, metrics, and artifacts for every training run so you can compare them later.
2. **Model Registry** -- promote a trained model from "experiment" to "production" with versioning, so you always know which model is live.

The other two components (Projects for reproducible runs, and Serving for REST API deployment) exist but are less commonly used.

## Why You Might Want It

The pipeline has four trainable models (`SellProbabilityModel`, `RevenueEstimator`, `MarginEstimator`, `MultiplePredictor`), each with hyperparameters that affect performance. When you start training these, you'll quickly accumulate dozens of runs:

- "XGBoost with depth=6, lr=0.05, focal_gamma=2.0 → val_auc=0.82"
- "XGBoost with depth=8, lr=0.03, focal_gamma=1.5 → val_auc=0.79"
- "LightGBM with num_leaves=31, lr=0.05 → val_mae=1.2"

Without tracking, this lives in your head, a notebook, or scattered print statements. MLflow gives you a searchable table of every run with a web UI to compare them visually.

The model registry solves a different problem: "which model file should the pipeline actually load?" Instead of hardcoding a file path like `models/sell_probability/2024-03-15.json`, you register a model as `sell-probability-prod` and the pipeline loads whatever version is current. When you retrain, you promote the new version and the pipeline picks it up automatically.

## When to Add It

Add MLflow when:
- You're running 10+ training experiments and losing track of what you tried
- Multiple model versions exist and you need to know which one is "production"
- You want to compare metrics across runs visually (the web UI is genuinely useful)

You do NOT need it when:
- You've trained fewer than ~5 runs total
- You're the only developer and can track experiments in a notebook
- You haven't started training models yet (current state)

**Simpler alternative that works today:** a CSV file of experiment results and a `models/` directory with dated filenames. Upgrade to MLflow when the CSV gets unwieldy.

## How to Add It to This Pipeline

### 1. Install

```bash
pip install mlflow>=2.12
```

### 2. Start the Server

No Docker needed. Run locally:

```bash
mlflow server --host 127.0.0.1 --port 5000 \
    --backend-store-uri sqlite:///mlflow.db \
    --default-artifact-root ./mlflow-artifacts
```

This stores everything locally in a SQLite database and a local directory. Open http://localhost:5000 to see the web UI.

### 3. Add Tracking to Model Training

Modify the existing `.train()` methods to log to MLflow. For example, in `signal_detection/model.py`:

```python
import mlflow

class SellProbabilityModel:
    def train(self, X, y, val_X=None, val_y=None):
        mlflow.set_experiment("sell-probability")

        with mlflow.start_run():
            params = {
                "max_depth": 6,
                "learning_rate": 0.05,
                "focal_alpha": 0.25,
                "focal_gamma": 2.0,
                "num_boost_round": 500,
            }
            mlflow.log_params(params)

            # ... existing training code ...

            mlflow.log_metrics({
                "train_auc": train_auc,
                "val_auc": val_auc,
            })

            # Save the model as an MLflow artifact
            mlflow.xgboost.log_model(self._model, "model")
```

Do the same for `RevenueEstimator`, `MarginEstimator`, and `MultiplePredictor`.

### 4. Register Models

After training, promote the best run to the registry:

```python
mlflow.register_model("runs:/abc123/model", "sell-probability")
```

Or do it in the web UI -- click a run, click "Register Model."

### 5. Load in the Pipeline

Replace file-based model loading with registry loading:

```python
import mlflow

# Load whatever version is current
model = mlflow.xgboost.load_model("models:/sell-probability/latest")
```

Or in the pipeline constructor:

```python
from src.signal_detection.model import SellProbabilityModel

sell_model = SellProbabilityModel()
sell_model.load_from_mlflow("sell-probability", version="latest")
```

### 6. Add a Config Setting

```python
# src/common/config.py
class MLflowSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MLFLOW_")
    tracking_uri: str = "http://localhost:5000"
    experiment_name: str = "deal-sourcing"
```

```env
# .env
MLFLOW_TRACKING_URI=http://localhost:5000
```

## Cost

Free. MLflow is Apache 2.0 licensed. Databricks offers a managed version, but the open-source version running locally or on a VM is functionally identical for a small team. Storage cost is negligible -- model artifacts are typically a few MB each.
