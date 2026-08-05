"""Local provider implementations."""

from economic_evidence_graph.providers.artifacts import FileArtifactStore, InMemoryArtifactStore
from economic_evidence_graph.providers.embeddings import DeterministicEconomicsEmbedding
from economic_evidence_graph.providers.http import GovernedHttpClient, GovernedResponse
from economic_evidence_graph.providers.memory import InMemoryRepository
from economic_evidence_graph.providers.runtime import DeterministicIdFactory, FrozenClock, RandomIdFactory, SystemClock
from economic_evidence_graph.providers.search import InMemorySearchIndex, OpenSearchIndex

__all__ = [
    "DeterministicEconomicsEmbedding",
    "DeterministicIdFactory",
    "FileArtifactStore",
    "FrozenClock",
    "GovernedHttpClient",
    "GovernedResponse",
    "InMemoryArtifactStore",
    "InMemoryRepository",
    "InMemorySearchIndex",
    "OpenSearchIndex",
    "RandomIdFactory",
    "SystemClock",
]
