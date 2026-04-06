"""Connected components clustering with Union-Find for entity merging."""

from uuid import uuid4

import structlog

logger = structlog.get_logger("entity_resolution.clustering")


class UnionFind:
    """Weighted union-find with path compression."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int) -> None:
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1

    def connected(self, x: int, y: int) -> bool:
        return self.find(x) == self.find(y)


class EntityClusterer:
    """Clusters matched records into entities using Union-Find."""

    def cluster(
        self,
        num_records: int,
        merge_pairs: list[tuple[int, int, float]],
    ) -> dict[int, str]:
        """Assign entity IDs to records based on merge pairs.

        Returns: dict mapping record_index -> entity_id (UUID string).
        """
        uf = UnionFind(num_records)

        for i, j, _score in merge_pairs:
            uf.union(i, j)

        # Map each component root to a stable UUID
        root_to_entity: dict[int, str] = {}
        record_to_entity: dict[int, str] = {}

        for idx in range(num_records):
            root = uf.find(idx)
            if root not in root_to_entity:
                root_to_entity[root] = str(uuid4())
            record_to_entity[idx] = root_to_entity[root]

        num_entities = len(root_to_entity)
        logger.info(
            "clustering.complete",
            num_records=num_records,
            num_entities=num_entities,
            merge_pairs=len(merge_pairs),
        )
        return record_to_entity
