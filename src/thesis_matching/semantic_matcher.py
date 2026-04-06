"""Semantic similarity matching between thesis descriptions and company descriptions."""

import numpy as np
import structlog

logger = structlog.get_logger("thesis_matching.semantic")


class SemanticMatcher:
    """Computes semantic similarity between thesis and company descriptions.

    Uses sentence-transformers for embedding, with cosine similarity scoring.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self._model_name)
        logger.info("semantic_matcher.model_loaded", model=self._model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        self._load_model()
        return self._model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]

    def score_pairs(
        self,
        thesis_descriptions: list[str],
        company_descriptions: list[str],
    ) -> np.ndarray:
        """Compute cosine similarity between each thesis-company pair.

        Returns matrix of shape (len(theses), len(companies)).
        """
        self._load_model()
        thesis_embs = self.encode(thesis_descriptions)
        company_embs = self.encode(company_descriptions)

        # Cosine similarity (already L2-normalized)
        similarity_matrix = thesis_embs @ company_embs.T
        return similarity_matrix

    def rank_companies(
        self,
        thesis_description: str,
        company_descriptions: list[str],
        company_ids: list[str],
    ) -> list[tuple[str, float]]:
        """Rank companies by semantic similarity to a thesis.

        Returns list of (company_id, similarity_score) sorted descending.
        """
        scores = self.score_pairs([thesis_description], company_descriptions)[0]
        ranked = sorted(
            zip(company_ids, scores.tolist()),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked
