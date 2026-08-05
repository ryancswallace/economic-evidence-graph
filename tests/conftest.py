"""Shared deterministic application composition for tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from economic_evidence_graph.application import CatalogService, EvidencePackExporter, JobService
from economic_evidence_graph.bootstrap import Container
from economic_evidence_graph.domain import EvidenceBundle
from economic_evidence_graph.fixtures import build_platform_fixture
from economic_evidence_graph.providers import (
    DeterministicIdFactory,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryRepository,
)


@dataclass(frozen=True, slots=True)
class MemorySystem:
    """Fully composed test system and its accepted fixture."""

    container: Container
    repository: InMemoryRepository
    artifacts: InMemoryArtifactStore
    bundle: EvidenceBundle


@pytest.fixture
def memory_system() -> MemorySystem:
    """Return the complete in-memory provider composition."""
    repository = InMemoryRepository()
    artifacts = InMemoryArtifactStore()
    clock = FrozenClock(datetime(2026, 8, 4, 16, 0, tzinfo=UTC))
    exporter = EvidencePackExporter(catalog=repository, artifacts=artifacts, clock=clock)
    container = Container(
        catalog_repository=repository,
        job_repository=repository,
        artifacts=artifacts,
        catalog=CatalogService(repository),
        jobs=JobService(
            jobs=repository,
            exporter=exporter,
            clock=clock,
            ids=DeterministicIdFactory("test-jobs"),
        ),
    )
    bundle = build_platform_fixture(artifacts)
    container.catalog.ingest(bundle)
    return MemorySystem(container=container, repository=repository, artifacts=artifacts, bundle=bundle)
