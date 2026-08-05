"""Application composition roots."""

from dataclasses import dataclass

import httpx
from sqlalchemy import create_engine

from economic_evidence_graph.application import (
    AgentToolService,
    CatalogService,
    CorpusIngestionService,
    EvidencePackExporter,
    JobService,
    ProjectService,
    RetrievalService,
    SearchIndexer,
    SearchProjector,
)
from economic_evidence_graph.config import Settings
from economic_evidence_graph.connectors import (
    BoardFomcConnector,
    BostonFedConnector,
    CoreConnector,
    CrossrefConnector,
    RepecRsyncConnector,
)
from economic_evidence_graph.exceptions import ConfigurationError
from economic_evidence_graph.parsers import DocumentParserRouter
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.ports import ArtifactStore, CatalogRepository, JobRepository, SearchIndex
from economic_evidence_graph.ports.connectors import SourceConnector
from economic_evidence_graph.providers import (
    DeterministicEconomicsEmbedding,
    FileArtifactStore,
    GovernedHttpClient,
    OpenSearchIndex,
    RandomIdFactory,
    SystemClock,
)
from economic_evidence_graph.providers.postgres import SqlRepository


@dataclass(frozen=True, slots=True)
class Container:
    """Explicit dependency container shared by the API and workers."""

    catalog_repository: CatalogRepository
    job_repository: JobRepository
    artifacts: ArtifactStore
    catalog: CatalogService
    jobs: JobService
    retrieval: RetrievalService | None = None
    indexer: SearchIndexer | None = None
    search_index: SearchIndex | None = None
    projects: ProjectService | None = None
    tools: AgentToolService | None = None

    def close(self) -> None:
        """Release provider resources exactly once per distinct repository."""
        self.catalog_repository.close()
        if self.job_repository is not self.catalog_repository:
            self.job_repository.close()
        if self.search_index is not None:
            self.search_index.close()


@dataclass(frozen=True, slots=True)
class CorpusContainer:
    """Complete local Step 2 ingestion composition."""

    repository: SqlRepository
    artifacts: FileArtifactStore
    http: GovernedHttpClient
    ingestion: CorpusIngestionService
    connectors: dict[str, SourceConnector]

    def close(self) -> None:
        """Release network and database pools."""
        self.http.close()
        self.repository.close()


def create_local_container(settings: Settings) -> Container:
    """Compose the complete initial local runtime.

    Args:
        settings: Validated local configuration.

    Returns:
        Application services backed by PostgreSQL and the local artifact store.
    """
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    repository = SqlRepository(engine)
    artifacts = FileArtifactStore(settings.artifact_root)
    clock = SystemClock()
    ids = RandomIdFactory()
    registry = SourceRegistry.load(settings.source_registry_path)
    policy = PolicyEngine(registry)
    embeddings = DeterministicEconomicsEmbedding()
    search_index = OpenSearchIndex(settings.opensearch_url, timeout=settings.request_timeout_seconds)
    exporter = EvidencePackExporter(catalog=repository, artifacts=artifacts, clock=clock)
    retrieval = RetrievalService(
        repository=repository,
        index=search_index,
        embeddings=embeddings,
        policy=policy,
        clock=clock,
        ids=ids,
    )
    projects = ProjectService(
        repository=repository,
        search_repository=repository,
        search_index=search_index,
        retrieval=retrieval,
        artifacts=artifacts,
        clock=clock,
    )
    return Container(
        catalog_repository=repository,
        job_repository=repository,
        artifacts=artifacts,
        catalog=CatalogService(repository),
        jobs=JobService(
            jobs=repository,
            exporter=exporter,
            clock=clock,
            ids=ids,
        ),
        retrieval=retrieval,
        indexer=SearchIndexer(
            repository=repository,
            index=search_index,
            projector=SearchProjector(registry=registry, embeddings=embeddings),
            embeddings=embeddings,
            clock=clock,
        ),
        search_index=search_index,
        projects=projects,
        tools=AgentToolService(
            retrieval=retrieval,
            projects=projects,
            audit=repository,
            clock=clock,
            ids=ids,
        ),
    )


def create_local_corpus_container(settings: Settings) -> CorpusContainer:
    """Compose policy, network, connectors, parsing, storage, and SQL persistence."""
    registry = SourceRegistry.load(settings.source_registry_path)
    policy = PolicyEngine(registry)
    clock = SystemClock()
    ids = RandomIdFactory()
    repository = SqlRepository(create_engine(settings.database_url, pool_pre_ping=True))
    artifacts = FileArtifactStore(settings.artifact_root)
    governed_http = GovernedHttpClient(
        client=httpx.Client(timeout=settings.request_timeout_seconds),
        registry=registry,
        policy=policy,
        clock=clock,
        ids=ids,
        crawler_email=settings.crawler_email,
    )
    connectors: dict[str, SourceConnector] = {
        "repec": RepecRsyncConnector(governed_http, policy=policy, clock=clock, ids=ids),
        "crossref": CrossrefConnector(governed_http, crawler_email=settings.crawler_email),
        "boston_fed": BostonFedConnector(governed_http),
        "board_fomc": BoardFomcConnector(governed_http),
    }
    if settings.core_api_key is not None:
        connectors["core"] = CoreConnector(governed_http, api_key=settings.core_api_key)
    return CorpusContainer(
        repository=repository,
        artifacts=artifacts,
        http=governed_http,
        ingestion=CorpusIngestionService(
            repository=repository,
            artifacts=artifacts,
            parsers=DocumentParserRouter(),
            policy=policy,
            clock=clock,
            ids=ids,
        ),
        connectors=connectors,
    )


def require_connector(container: CorpusContainer, source_code: str) -> SourceConnector:
    """Return a configured connector without revealing absent credentials."""
    try:
        return container.connectors[source_code]
    except KeyError as error:
        raise ConfigurationError(f"connector is not configured: {source_code}") from error
