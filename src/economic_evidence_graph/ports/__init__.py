"""Infrastructure and connector protocols."""

from economic_evidence_graph.ports.artifacts import ArtifactRef, ArtifactStore
from economic_evidence_graph.ports.connectors import (
    AcquisitionBatch,
    AcquisitionCandidate,
    AcquisitionFailureCandidate,
    DiscoveryPage,
    DiscoveryRecord,
    SourceConnector,
)
from economic_evidence_graph.ports.projects import ProjectRepository
from economic_evidence_graph.ports.repositories import CatalogRepository, CorpusRepository, JobRepository
from economic_evidence_graph.ports.runtime import Clock, IdFactory
from economic_evidence_graph.ports.search import (
    EmbeddingProvider,
    SearchIndex,
    SearchIndexFilters,
    SearchIndexHit,
    SearchRepository,
)
from economic_evidence_graph.ports.tools import ToolAuditRepository

__all__ = [
    "AcquisitionBatch",
    "AcquisitionCandidate",
    "AcquisitionFailureCandidate",
    "ArtifactRef",
    "ArtifactStore",
    "CatalogRepository",
    "Clock",
    "CorpusRepository",
    "DiscoveryPage",
    "DiscoveryRecord",
    "EmbeddingProvider",
    "IdFactory",
    "JobRepository",
    "ProjectRepository",
    "SearchIndex",
    "SearchIndexFilters",
    "SearchIndexHit",
    "SearchRepository",
    "SourceConnector",
    "ToolAuditRepository",
]
