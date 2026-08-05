"""Catalog use cases."""

from dataclasses import dataclass

from economic_evidence_graph.application.canonicalization import CanonicalWorkProjector
from economic_evidence_graph.domain import EvidenceBundle
from economic_evidence_graph.exceptions import NotFoundError
from economic_evidence_graph.ports import CatalogRepository
from economic_evidence_graph.ports.search import SearchRepository


@dataclass(frozen=True, slots=True)
class CatalogService:
    """Application boundary for canonical work ingestion and retrieval."""

    repository: CatalogRepository
    corpus_repository: SearchRepository | None = None
    projector: CanonicalWorkProjector | None = None

    def ingest(self, bundle: EvidenceBundle) -> EvidenceBundle:
        """Validate and persist an evidence bundle idempotently."""
        bundle.validate_lineage()
        self.repository.put_bundle(bundle)
        return self.repository.get_bundle(bundle.work.id)

    def get_work(self, work_id: str) -> EvidenceBundle:
        """Return a provenance-complete work bundle."""
        try:
            return self.repository.get_bundle(work_id)
        except NotFoundError:
            if self.corpus_repository is None or self.projector is None:
                raise
        return self.projector.project(self.corpus_repository.list_corpus_search_records(), work_id)

    def get_bundle(self, work_id: str) -> EvidenceBundle:
        """Expose the canonical bundle-reader contract used by downstream exports."""
        return self.get_work(work_id)
