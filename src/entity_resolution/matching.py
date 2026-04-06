"""Rule-based entity matching for MVP. Upgradeable to learned model in Phase 4."""

import re

import structlog

logger = structlog.get_logger("entity_resolution.matching")


def _normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"\b(inc|llc|ltd|corp|co|company|limited|incorporated)\b\.?", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _jaro_winkler(s1: str, s2: str) -> float:
    """Jaro-Winkler similarity. Falls back to simple ratio if jellyfish unavailable."""
    try:
        import jellyfish

        return jellyfish.jaro_winkler_similarity(s1, s2)
    except ImportError:
        # Simple fallback: character overlap ratio
        if not s1 or not s2:
            return 0.0
        common = set(s1) & set(s2)
        return len(common) / max(len(set(s1)), len(set(s2)))


def _domain_similarity(d1: str | None, d2: str | None) -> float:
    if not d1 or not d2:
        return 0.0
    d1 = d1.lower().strip().replace("www.", "")
    d2 = d2.lower().strip().replace("www.", "")
    return 1.0 if d1 == d2 else 0.0


def _geography_similarity(r1: dict, r2: dict) -> float:
    c1 = (r1.get("hq_country") or "").lower()
    c2 = (r2.get("hq_country") or "").lower()
    s1 = (r1.get("hq_state") or "").lower()
    s2 = (r2.get("hq_state") or "").lower()

    if c1 and c2 and c1 != c2:
        return 0.0
    if s1 and s2 and s1 == s2:
        return 1.0
    if c1 and c2 and c1 == c2:
        return 0.5
    return 0.0


def _executive_overlap(r1: dict, r2: dict) -> float:
    execs1 = {e.get("name", "").lower() for e in r1.get("executives", []) if e.get("name")}
    execs2 = {e.get("name", "").lower() for e in r2.get("executives", []) if e.get("name")}
    if not execs1 or not execs2:
        return 0.0
    overlap = execs1 & execs2
    return len(overlap) / min(len(execs1), len(execs2))


# Feature weights for the composite match score
WEIGHTS = {
    "name": 0.35,
    "domain": 0.30,
    "geography": 0.15,
    "executives": 0.20,
}


class RuleBasedMatcher:
    """Computes pairwise similarity scores between company records."""

    def score_pair(self, record_a: dict, record_b: dict) -> float:
        name_a = _normalize_name(record_a.get("name", ""))
        name_b = _normalize_name(record_b.get("name", ""))
        name_sim = _jaro_winkler(name_a, name_b)

        domain_sim = _domain_similarity(
            record_a.get("domain"), record_b.get("domain")
        )
        geo_sim = _geography_similarity(record_a, record_b)
        exec_sim = _executive_overlap(record_a, record_b)

        score = (
            WEIGHTS["name"] * name_sim
            + WEIGHTS["domain"] * domain_sim
            + WEIGHTS["geography"] * geo_sim
            + WEIGHTS["executives"] * exec_sim
        )
        return score

    def match_candidates(
        self,
        records: list[dict],
        candidate_pairs: set[tuple[int, int]],
        auto_merge_threshold: float = 0.85,
        review_threshold: float = 0.60,
    ) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:
        """Score all candidate pairs. Returns (auto_merges, review_queue)."""
        auto_merges = []
        review_queue = []

        for i, j in candidate_pairs:
            score = self.score_pair(records[i], records[j])
            if score >= auto_merge_threshold:
                auto_merges.append((i, j, score))
            elif score >= review_threshold:
                review_queue.append((i, j, score))

        logger.info(
            "matching.complete",
            auto_merges=len(auto_merges),
            review_queue=len(review_queue),
            total_pairs=len(candidate_pairs),
        )
        return auto_merges, review_queue
