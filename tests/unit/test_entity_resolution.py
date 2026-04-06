"""Tests for entity resolution pipeline."""

import pytest

from src.entity_resolution.blocking import LSHBlocker
from src.entity_resolution.clustering import EntityClusterer, UnionFind
from src.entity_resolution.matching import RuleBasedMatcher


class TestLSHBlocker:
    def test_same_prefix_blocked_together(self):
        records = [
            {"name": "Acme Corp", "domain": "acme.com", "hq_country": "US"},
            {"name": "Acme Corporation", "domain": "acme.com", "hq_country": "US"},
            {"name": "Zebra Inc", "domain": "zebra.io", "hq_country": "US"},
        ]
        blocker = LSHBlocker()
        pairs = blocker.get_candidate_pairs(records)
        # Acme Corp and Acme Corporation should be candidates
        assert (0, 1) in pairs
        # Zebra should not be paired with Acme (different prefix and domain)
        assert (0, 2) not in pairs or (1, 2) not in pairs

    def test_domain_blocking(self):
        records = [
            {"name": "Company A", "domain": "example.com", "hq_country": "US"},
            {"name": "Different Name", "domain": "example.com", "hq_country": "US"},
        ]
        blocker = LSHBlocker()
        pairs = blocker.get_candidate_pairs(records)
        assert (0, 1) in pairs

    def test_empty_records(self):
        blocker = LSHBlocker()
        pairs = blocker.get_candidate_pairs([])
        assert len(pairs) == 0


class TestRuleBasedMatcher:
    def test_identical_companies_high_score(self):
        matcher = RuleBasedMatcher()
        a = {"name": "Acme Corp", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []}
        b = {"name": "Acme Corp", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []}
        score = matcher.score_pair(a, b)
        assert score >= 0.79  # name(0.35) + domain(0.30) + geo(0.15) = 0.80

    def test_different_companies_low_score(self):
        matcher = RuleBasedMatcher()
        a = {"name": "Acme Corp", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []}
        b = {"name": "Zebra Inc", "domain": "zebra.io", "hq_country": "GB", "hq_state": "", "executives": []}
        score = matcher.score_pair(a, b)
        assert score < 0.5

    def test_name_normalization(self):
        matcher = RuleBasedMatcher()
        a = {"name": "Acme Inc.", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []}
        b = {"name": "ACME Corporation", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []}
        score = matcher.score_pair(a, b)
        # Same domain + same geo = high score even with fuzzy name
        assert score > 0.5

    def test_match_candidates_separation(self):
        matcher = RuleBasedMatcher()
        records = [
            {"name": "Acme Corp", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []},
            {"name": "Acme Corporation", "domain": "acme.com", "hq_country": "US", "hq_state": "CA", "executives": []},
            {"name": "Zebra Inc", "domain": "zebra.io", "hq_country": "GB", "hq_state": "", "executives": []},
        ]
        pairs = {(0, 1), (0, 2), (1, 2)}
        auto, review = matcher.match_candidates(
            records, pairs, auto_merge_threshold=0.75, review_threshold=0.50
        )
        # Acme pair should at least be in auto-merge or review (same domain, close name)
        all_matches = auto + review
        assert any(
            (i == 0 and j == 1) or (i == 1 and j == 0) for i, j, _ in all_matches
        )


class TestUnionFind:
    def test_basic_union(self):
        uf = UnionFind(5)
        uf.union(0, 1)
        uf.union(2, 3)
        assert uf.connected(0, 1)
        assert uf.connected(2, 3)
        assert not uf.connected(0, 2)

    def test_transitive_union(self):
        uf = UnionFind(5)
        uf.union(0, 1)
        uf.union(1, 2)
        assert uf.connected(0, 2)


class TestEntityClusterer:
    def test_basic_clustering(self):
        clusterer = EntityClusterer()
        merge_pairs = [(0, 1, 0.9), (2, 3, 0.85)]
        result = clusterer.cluster(5, merge_pairs)

        assert len(result) == 5
        assert result[0] == result[1]  # Merged
        assert result[2] == result[3]  # Merged
        assert result[0] != result[2]  # Separate clusters
        assert result[4] != result[0]  # Singleton

    def test_empty_merges(self):
        clusterer = EntityClusterer()
        result = clusterer.cluster(3, [])
        # All singletons — each gets unique entity ID
        assert len(set(result.values())) == 3
