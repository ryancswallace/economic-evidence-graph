"""Search read-model, index, and embedding provider protocols."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from economic_evidence_graph.domain import (
    CorpusSearchRecord,
    IndexGeneration,
    SearchEvidenceDocument,
    SearchRun,
    SearchWorkDocument,
    TemporalSafetyClass,
)


@dataclass(frozen=True, slots=True)
class SearchIndexFilters:
    """Provider-neutral filters applied inside every candidate query."""

    source_codes: tuple[str, ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    eligible_at: datetime | None = None
    allowed_safety_classes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchIndexHit:
    """Raw provider hit with no application-level trust assumptions."""

    document: dict[str, object]
    score: float
    rank: int


class EmbeddingProvider(Protocol):
    """Replaceable local/AWS-compatible vectorization boundary."""

    @property
    def config_id(self) -> str:
        """Return an immutable algorithm/model configuration identifier."""
        ...

    @property
    def dimension(self) -> int:
        """Return the fixed vector dimension."""
        ...

    @property
    def temporal_safety_class(self) -> TemporalSafetyClass:
        """Return the historical-safety class for produced vectors."""
        ...

    def embed(self, text: str) -> tuple[float, ...]:
        """Create one normalized vector."""
        ...


class SearchIndex(Protocol):
    """Versioned index generation and candidate-retrieval boundary."""

    def replace_generation(
        self,
        generation: IndexGeneration,
        work_documents: tuple[SearchWorkDocument, ...],
        evidence_documents: tuple[SearchEvidenceDocument, ...],
        *,
        embedding_dimension: int,
    ) -> None:
        """Build, validate, and atomically activate one immutable generation."""
        ...

    def search(
        self,
        *,
        index_kind: str,
        channel: str,
        query: str,
        query_vector: tuple[float, ...] | None,
        filters: SearchIndexFilters,
        limit: int,
    ) -> tuple[SearchIndexHit, ...]:
        """Run one bounded channel after provider-side eligibility filtering."""
        ...

    def get_evidence(self, evidence_unit_id: str) -> dict[str, object] | None:
        """Return one evidence projection from the active generation."""
        ...

    def get_work_versions(self, work_id: str, *, limit: int) -> tuple[dict[str, object], ...]:
        """Return bounded version projections for one exact canonical work."""
        ...

    def list_version_evidence(
        self,
        document_version_id: str,
        *,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        """Return bounded evidence projections for one exact document version."""
        ...

    def check(self) -> None:
        """Raise when the search provider is unavailable."""
        ...

    def close(self) -> None:
        """Release provider-owned resources."""
        ...


class SearchRepository(Protocol):
    """Canonical persistence used to rebuild and reproduce search."""

    def list_corpus_search_records(self) -> tuple[CorpusSearchRecord, ...]:
        """Return all immutable, joined Step 2 records for projection."""
        ...

    def list_suppression_scopes(self) -> tuple[tuple[str, str], ...]:
        """Return current delivery tombstones as scope/value pairs."""
        ...

    def put_index_generation(self, item: IndexGeneration) -> None:
        """Persist an activated immutable index generation."""
        ...

    def latest_index_generation(self) -> IndexGeneration | None:
        """Return the most recently activated generation."""
        ...

    def get_index_generation(self, generation_id: str) -> IndexGeneration | None:
        """Return one generation by identifier."""
        ...

    def put_search_run(self, item: SearchRun) -> None:
        """Persist an immutable reproducibility manifest."""
        ...

    def get_search_run(self, run_id: str) -> SearchRun | None:
        """Return a saved run by identifier."""
        ...
