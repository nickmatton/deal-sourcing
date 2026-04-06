"""Dagster assets for data ingestion pipeline."""

from datetime import datetime, timedelta, timezone

from src.common.logging import PipelineStage, log_stage, log_step


async def ingest_pitchbook_companies(
    api_key: str, lookback_days: int = 7
) -> list[dict]:
    """Dagster asset: Fetch companies from PitchBook."""
    from src.ingestion.connectors.pitchbook import PitchBookConnector

    with log_stage(PipelineStage.INGESTION, source="pitchbook", asset="companies") as log:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        connector = PitchBookConnector(api_key=api_key)
        try:
            companies = await connector.fetch_companies(since=since)
            log.info("fetched", count=len(companies), lookback_days=lookback_days)
            return [c.model_dump() for c in companies]
        finally:
            await connector.close()


async def ingest_crunchbase_companies(
    api_key: str, lookback_days: int = 7
) -> list[dict]:
    """Dagster asset: Fetch companies from Crunchbase."""
    from src.ingestion.connectors.crunchbase import CrunchbaseConnector

    with log_stage(PipelineStage.INGESTION, source="crunchbase", asset="companies") as log:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        connector = CrunchbaseConnector(api_key=api_key)
        try:
            companies = await connector.fetch_companies(since=since)
            log.info("fetched", count=len(companies), lookback_days=lookback_days)
            return [c.model_dump() for c in companies]
        finally:
            await connector.close()


async def ingest_pitchbook_transactions(
    api_key: str, lookback_days: int = 7
) -> list[dict]:
    """Dagster asset: Fetch transactions from PitchBook."""
    from src.ingestion.connectors.pitchbook import PitchBookConnector

    with log_stage(PipelineStage.INGESTION, source="pitchbook", asset="transactions") as log:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        connector = PitchBookConnector(api_key=api_key)
        try:
            transactions = await connector.fetch_transactions(since=since)
            log.info("fetched", count=len(transactions), lookback_days=lookback_days)
            return [t.model_dump() for t in transactions]
        finally:
            await connector.close()


async def resolve_and_normalize(
    raw_companies: list[dict],
) -> list[dict]:
    """Dagster asset: Entity resolution + normalization (bronze → silver)."""
    from src.common.schemas.ingestion import CompanyRaw
    from src.entity_resolution.engine import EntityResolutionEngine
    from src.ingestion.normalizers.company import normalize_company

    with log_stage(PipelineStage.ENTITY_RESOLUTION, input_count=len(raw_companies)) as log:
        engine = EntityResolutionEngine()
        raws = [CompanyRaw.model_validate(c) for c in raw_companies]

        normalized = []
        for raw in raws:
            entity_id = engine.resolve(raw)
            norm = normalize_company(raw, entity_id)
            normalized.append(norm.model_dump())

        log.info("resolved", output_entities=len(normalized))
        return normalized
