"""LSH-based blocking to reduce candidate pairs for entity resolution."""

import re
from collections import defaultdict

import structlog

logger = structlog.get_logger("entity_resolution.blocking")


def _normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"\b(inc|llc|ltd|corp|co|company|limited|incorporated)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _shingle(text: str, k: int = 3) -> set[str]:
    if len(text) < k:
        return {text}
    return {text[i : i + k] for i in range(len(text) - k + 1)}


class LSHBlocker:
    """MinHash LSH blocking for entity resolution.

    Groups records into blocks by approximate similarity on
    name + domain + geography. Only records in the same block
    are compared by the matcher.
    """

    def __init__(
        self,
        num_perm: int = 128,
        threshold: float = 0.3,
    ) -> None:
        self._num_perm = num_perm
        self._threshold = threshold
        self._blocks: dict[str, list[str]] = defaultdict(list)

    def _make_signature(self, record: dict) -> str:
        parts = []
        if record.get("name"):
            parts.append(_normalize_name(record["name"]))
        if record.get("domain"):
            parts.append(record["domain"].lower().strip())
        if record.get("hq_country"):
            parts.append(record["hq_country"].lower().strip())
        return " ".join(parts)

    def build_blocks(self, records: list[dict]) -> dict[str, list[int]]:
        """Build blocking groups using character n-gram shingling.

        Returns a dict mapping block_key -> list of record indices.
        For MVP, uses simple prefix blocking + n-gram overlap.
        Full LSH with MinHash (datasketch) used in production.
        """
        blocks: dict[str, list[int]] = defaultdict(list)

        for idx, record in enumerate(records):
            sig = self._make_signature(record)
            if not sig:
                continue

            # Prefix blocking: first 4 chars of normalized name
            name_norm = _normalize_name(record.get("name", ""))
            if len(name_norm) >= 4:
                blocks[f"prefix:{name_norm[:4]}"].append(idx)

            # Domain blocking: exact domain match
            domain = record.get("domain", "")
            if domain:
                domain_clean = domain.lower().strip()
                blocks[f"domain:{domain_clean}"].append(idx)

            # Geography blocking: country + first 3 chars of name
            country = record.get("hq_country", "")
            if country and len(name_norm) >= 3:
                blocks[f"geo:{country.lower()}:{name_norm[:3]}"].append(idx)

        # Filter blocks with only 1 record (no pairs to compare)
        blocks = {k: v for k, v in blocks.items() if len(v) > 1}
        logger.info("blocking.complete", num_blocks=len(blocks), num_records=len(records))
        return blocks

    def get_candidate_pairs(self, records: list[dict]) -> set[tuple[int, int]]:
        """Return deduplicated set of (i, j) candidate pairs to compare."""
        blocks = self.build_blocks(records)
        pairs: set[tuple[int, int]] = set()

        for indices in blocks.values():
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    pair = (min(indices[i], indices[j]), max(indices[i], indices[j]))
                    pairs.add(pair)

        logger.info("blocking.candidate_pairs", count=len(pairs))
        return pairs
