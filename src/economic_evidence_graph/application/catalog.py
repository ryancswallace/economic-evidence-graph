"""Catalog use cases."""

from dataclasses import dataclass

from economic_evidence_graph.domain import EvidenceBundle
from economic_evidence_graph.ports import CatalogRepository


@dataclass(frozen=True, slots=True)
class CatalogService:
    """Application boundary for canonical work ingestion and retrieval."""

    repository: CatalogRepository

    def ingest(self, bundle: EvidenceBundle) -> EvidenceBundle:
        """Validate and persist an evidence bundle idempotently."""
        bundle.validate_lineage()
        self.repository.put_bundle(bundle)
        return self.repository.get_bundle(bundle.work.id)

    def get_work(self, work_id: str) -> EvidenceBundle:
        """Return a provenance-complete work bundle."""
        return self.repository.get_bundle(work_id)
