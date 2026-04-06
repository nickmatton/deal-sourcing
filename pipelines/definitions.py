"""Dagster pipeline definitions for the deal sourcing system.

This file defines the asset graph that Dagster orchestrates.
Assets represent data artifacts; Dagster tracks lineage, freshness, and dependencies.
"""

# NOTE: This is the pipeline structure. Actual Dagster @asset decorators
# require `dagster` to be installed. This module serves as the pipeline
# definition that Dagster will load.

DAILY_ASSETS = [
    "raw_pitchbook_companies",
    "raw_pitchbook_transactions",
    "raw_crunchbase_companies",
    "market_context_data",
    "resolved_entities",
    "feature_store_daily_refresh",
    "event_triggered_rescoring",
]

WEEKLY_ASSETS = [
    "full_universe_signal_scores",
    "thesis_match_rankings",
    "shadow_valuations",
    "alpha_scores",
    "outreach_priority_queue",
]

PIPELINE_SCHEDULE = {
    "daily_ingestion": {
        "cron": "0 6 * * *",  # 6 AM daily
        "assets": DAILY_ASSETS,
    },
    "weekly_scoring": {
        "cron": "0 8 * * 1",  # 8 AM Mondays
        "assets": WEEKLY_ASSETS,
    },
    "quarterly_retraining": {
        "cron": "0 0 1 */3 *",  # 1st of every 3rd month
        "assets": [
            "retrain_sell_probability",
            "retrain_revenue_estimator",
            "retrain_margin_estimator",
            "retrain_multiple_predictor",
        ],
    },
}
