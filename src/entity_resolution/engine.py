"""Top-level entity resolution engine that orchestrates blocking → matching → clustering."""

from datetime import datetime, timezone

import structlog

from src.common.audit import AuditAction, AuditEntry, AuditLogger
from src.common.entity import CanonicalEntity, EntityType, SourceRecord
from src.common.logging import log_step
from src.entity_resolution.blocking import LSHBlocker
from src.entity_resolution.clustering import EntityClusterer
from src.entity_resolution.matching import RuleBasedMatcher

logger = structlog.get_logger("entity_resolution")


class EntityResolutionEngine:
    """Orchestrates the full entity resolution pipeline.

    Usage:
        engine = EntityResolutionEngine()
        entities = engine.resolve_batch(raw_records)
    """

    def __init__(
        self,
        auto_merge_threshold: float = 0.85,
        review_threshold: float = 0.60,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._blocker = LSHBlocker()
        self._matcher = RuleBasedMatcher()
        self._clusterer = EntityClusterer()
        self._auto_merge_threshold = auto_merge_threshold
        self._review_threshold = review_threshold
        self._audit = audit_logger or AuditLogger()

        # In-memory entity cache for incremental resolution
        self._entity_cache: dict[str, CanonicalEntity] = {}
        self._source_to_entity: dict[str, str] = {}  # "source:source_id" -> entity_id

    def resolve(self, raw: "CompanyRaw") -> str:  # noqa: F821 — forward ref
        """Resolve a single raw record to an entity ID.

        For incremental ingestion: checks cache first, then assigns a new ID.
        Full batch resolution should use resolve_batch().
        """
        source_key = f"{raw.source}:{raw.source_id}"
        if source_key in self._source_to_entity:
            return self._source_to_entity[source_key]

        # No cached match — assign new entity ID
        now = datetime.now(timezone.utc).isoformat()
        entity = CanonicalEntity(
            entity_type=EntityType.COMPANY,
            canonical_name=raw.name,
            domain=raw.domain,
            geography=raw.hq_country,
            source_records=[
                SourceRecord(
                    source=raw.source,
                    source_id=raw.source_id,
                    raw_name=raw.name,
                    domain=raw.domain,
                    geography=raw.hq_country,
                    ingested_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        self._entity_cache[entity.entity_id] = entity
        self._source_to_entity[source_key] = entity.entity_id
        return entity.entity_id

    def resolve_batch(
        self, records: list[dict]
    ) -> tuple[dict[int, str], list[tuple[int, int, float]]]:
        """Run full blocking → matching → clustering on a batch.

        Returns:
            entity_map: dict mapping record_index -> entity_id
            review_queue: list of (i, j, score) pairs needing human review
        """
        logger.info("batch_start", num_records=len(records))

        with log_step("blocking", logger) as blog:
            candidate_pairs = self._blocker.get_candidate_pairs(records)
            blog.info("candidates", pairs=len(candidate_pairs))

        with log_step("matching", logger) as mlog:
            auto_merges, review_queue = self._matcher.match_candidates(
                records,
                candidate_pairs,
                auto_merge_threshold=self._auto_merge_threshold,
                review_threshold=self._review_threshold,
            )
            mlog.info("scored", auto=len(auto_merges), review=len(review_queue))

        with log_step("clustering", logger) as clog:
            entity_map = self._clusterer.cluster(len(records), auto_merges)
            clog.info("clustered", entities=len(set(entity_map.values())))

        for i, j, score in auto_merges:
            self._audit.log(AuditEntry(
                action=AuditAction.ENTITY_MERGE,
                actor="entity_resolution_engine",
                entity_id=entity_map[i],
                details={
                    "merged_records": [i, j],
                    "score": score,
                    "method": "auto_merge",
                },
                stage="0B",
            ))

        logger.info(
            "batch_complete",
            num_entities=len(set(entity_map.values())),
            auto_merges=len(auto_merges),
            pending_review=len(review_queue),
        )
        return entity_map, review_queue
