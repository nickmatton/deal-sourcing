"""Dataset accumulator — persists pipeline data to Parquet for ML training.

All data is stored under a configurable root directory (default: data/)
in append-friendly Parquet files organized by table:

    data/
      companies.parquet        # CompanyNormalized records
      transactions.parquet     # TransactionRecord (comps + EDGAR 8-K)
      valuations.parquet       # ShadowValuation outputs
      alpha_scores.parquet     # AlphaScore outputs
      underwriting.parquet     # UnderwritingResult outputs
      form_d_offerings.parquet # Form D private placements
      enrichment_log.parquet   # What was enriched, from where
      pipeline_runs.parquet    # Run metadata (timestamp, params, counts)

Each append call reads the existing file, concatenates new rows, deduplicates
on primary key, and writes back. This is simple and correct for datasets up to
~1M rows. For larger scale, migrate to Delta Lake / Iceberg.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger("dataset")


def _append_parquet(
    path: Path,
    new_df: pd.DataFrame,
    dedup_key: str | list[str] | None = None,
) -> int:
    """Append rows to a Parquet file, deduplicating on key if provided.

    Returns the total row count after append.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    if dedup_key and not combined.empty:
        combined = combined.drop_duplicates(subset=dedup_key, keep="last")

    combined.to_parquet(path, index=False)
    return len(combined)


class DatasetAccumulator:
    """Persists pipeline outputs to Parquet files for ML training."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._root = Path(data_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    # ─── Companies ───────────────────────────────────────────────────

    def save_companies(self, companies: list) -> int:
        """Persist CompanyNormalized records. Deduplicates on entity_id."""
        if not companies:
            return 0
        rows = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in companies]
        df = pd.DataFrame(rows)
        # Flatten nested types for Parquet compatibility
        for col in df.columns:
            if df[col].apply(lambda x: isinstance(x, (dict, list))).any():
                df[col] = df[col].apply(str)
        total = _append_parquet(self._root / "companies.parquet", df, "entity_id")
        logger.info("dataset.companies_saved", new=len(rows), total=total)
        return total

    # ─── Transactions ────────────────────────────────────────────────

    def save_transactions(self, transactions: list) -> int:
        """Persist TransactionRecord records. Deduplicates on transaction_id."""
        if not transactions:
            return 0
        rows = [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in transactions]
        df = pd.DataFrame(rows)
        total = _append_parquet(self._root / "transactions.parquet", df, "transaction_id")
        logger.info("dataset.transactions_saved", new=len(rows), total=total)
        return total

    # ─── Valuations ──────────────────────────────────────────────────

    def save_valuations(self, valuations: list) -> int:
        """Persist ShadowValuation records. Deduplicates on (entity_id, valued_at)."""
        if not valuations:
            return 0
        rows = []
        for v in valuations:
            d = v.model_dump() if hasattr(v, "model_dump") else dict(v)
            # Flatten tuple fields
            if isinstance(d.get("ev_range_80ci"), (tuple, list)):
                d["ev_range_low"] = d["ev_range_80ci"][0]
                d["ev_range_high"] = d["ev_range_80ci"][1]
                del d["ev_range_80ci"]
            for k, val in d.items():
                if isinstance(val, (dict, list)):
                    d[k] = str(val)
            rows.append(d)
        df = pd.DataFrame(rows)
        total = _append_parquet(
            self._root / "valuations.parquet", df, ["entity_id", "valued_at"]
        )
        logger.info("dataset.valuations_saved", new=len(rows), total=total)
        return total

    # ─── Alpha Scores ────────────────────────────────────────────────

    def save_alpha_scores(self, scores: list) -> int:
        """Persist AlphaScore records. Deduplicates on (entity_id, scored_at)."""
        if not scores:
            return 0
        rows = []
        for s in scores:
            d = s.model_dump() if hasattr(s, "model_dump") else dict(s)
            for k, val in d.items():
                if isinstance(val, (dict, list)):
                    d[k] = str(val)
            rows.append(d)
        df = pd.DataFrame(rows)
        total = _append_parquet(
            self._root / "alpha_scores.parquet", df, ["entity_id", "scored_at"]
        )
        logger.info("dataset.alpha_scores_saved", new=len(rows), total=total)
        return total

    # ─── Underwriting ────────────────────────────────────────────────

    def save_underwriting(self, results: list) -> int:
        """Persist UnderwritingResult records."""
        if not results:
            return 0
        rows = []
        for r in results:
            d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            # Flatten nested distribution objects and tuples
            for k, val in list(d.items()):
                if isinstance(val, dict):
                    for sub_k, sub_v in val.items():
                        d[f"{k}_{sub_k}"] = sub_v
                    del d[k]
                elif isinstance(val, (tuple, list)):
                    d[k] = str(val)
            rows.append(d)
        df = pd.DataFrame(rows)
        total = _append_parquet(
            self._root / "underwriting.parquet", df, ["entity_id", "simulated_at"]
        )
        logger.info("dataset.underwriting_saved", new=len(rows), total=total)
        return total

    # ─── Form D Offerings ────────────────────────────────────────────

    def save_form_d(self, offerings: list) -> int:
        """Persist Form D offering data from EDGAR."""
        if not offerings:
            return 0
        rows = []
        for o in offerings:
            d = o.__dict__ if hasattr(o, "__dict__") else dict(o)
            for k, val in d.items():
                if isinstance(val, (dict, list)):
                    d[k] = str(val)
            rows.append(d)
        df = pd.DataFrame(rows)
        total = _append_parquet(
            self._root / "form_d_offerings.parquet", df, ["cik", "filing_date"]
        )
        logger.info("dataset.form_d_saved", new=len(rows), total=total)
        return total

    # ─── Enrichment Log ──────────────────────────────────────────────

    def save_enrichment_log(self, results: list) -> int:
        """Persist enrichment results for provenance tracking."""
        if not results:
            return 0
        rows = []
        for r in results:
            d = r.__dict__ if hasattr(r, "__dict__") else dict(r)
            for k, val in d.items():
                if isinstance(val, (dict, list)):
                    d[k] = str(val)
            rows.append(d)
        df = pd.DataFrame(rows)
        df["timestamp"] = datetime.now(timezone.utc).isoformat()
        total = _append_parquet(self._root / "enrichment_log.parquet", df)
        logger.info("dataset.enrichment_log_saved", new=len(rows), total=total)
        return total

    # ─── Pipeline Run Metadata ───────────────────────────────────────

    def save_pipeline_run(self, **kwargs: Any) -> None:
        """Log a pipeline run with its parameters and result counts."""
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: str(v) if isinstance(v, (dict, list)) else v for k, v in kwargs.items()},
        }
        df = pd.DataFrame([row])
        _append_parquet(self._root / "pipeline_runs.parquet", df)
        logger.info("dataset.pipeline_run_logged", **kwargs)

    # ─── Reading ─────────────────────────────────────────────────────

    def load(self, table: str) -> pd.DataFrame:
        """Load a dataset table as a DataFrame."""
        path = self._root / f"{table}.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def stats(self) -> dict[str, int]:
        """Return row counts for all dataset tables."""
        counts = {}
        for path in sorted(self._root.glob("*.parquet")):
            try:
                pf = pq.ParquetFile(path)
                counts[path.stem] = pf.metadata.num_rows
            except Exception:
                counts[path.stem] = 0
        return counts
