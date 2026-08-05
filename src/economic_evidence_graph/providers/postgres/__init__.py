"""PostgreSQL provider and canonical schema."""

from economic_evidence_graph.providers.postgres.repository import SqlRepository
from economic_evidence_graph.providers.postgres.schema import metadata

__all__ = ["SqlRepository", "metadata"]
