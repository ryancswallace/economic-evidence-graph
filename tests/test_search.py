"""Step 3 projection, historical retrieval, provider, API, and CLI tests."""

from __future__ import annotations

import json
from asyncio import run
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine

from economic_evidence_graph import cli
from economic_evidence_graph.api import create_app
from economic_evidence_graph.application import RetrievalService, SearchIndexer, SearchProjector, availability_decision
from economic_evidence_graph.bootstrap import Container
from economic_evidence_graph.domain import (
    AcquiredAsset,
    AvailabilityDecision,
    ChannelContribution,
    ContentLink,
    CorpusSearchRecord,
    HistoricalCutoff,
    IdentifierValue,
    IndexGeneration,
    IngestedRecord,
    NormalizedDocument,
    NormalizedUnit,
    QualityReport,
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchRun,
    SourceChangeEvent,
    SourceMetadataRecord,
    SuppressionRecord,
    TemporalSafetyClass,
)
from economic_evidence_graph.domain.corpus import ChangeKind
from economic_evidence_graph.exceptions import (
    ConflictError,
    EconomicEvidenceGraphError,
    NotFoundError,
    PolicyDeniedError,
    SearchBackendError,
)
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.ports import SearchIndexFilters
from economic_evidence_graph.providers import (
    DeterministicEconomicsEmbedding,
    DeterministicIdFactory,
    FrozenClock,
    InMemoryRepository,
    InMemorySearchIndex,
    OpenSearchIndex,
)
from economic_evidence_graph.providers.postgres import SqlRepository
from economic_evidence_graph.providers.postgres.schema import metadata as sql_metadata

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)
_SHA_A = "a" * 64
_SHA_B = "b" * 64


class ApiClient:
    """Small synchronous facade over the ASGI interface."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str) -> httpx.Response:
        """Issue one GET request."""
        return run(self._request("GET", path))

    def post(self, path: str, *, json: object) -> httpx.Response:
        """Issue one JSON POST request."""
        return run(self._request("POST", path, json=json))

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Call the app through HTTPX's public transport."""
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)


def _ingested(
    key: str,
    *,
    source: str = "crossref",
    title: str = "Monetary Policy and Inflation Expectations",
    text: str = "Forward guidance and asset purchases lowered long-term interest rates.",
    observed_at: datetime = _NOW,
    published_at: date | None = date(2024, 1, 2),
    asserted_at: datetime | None = None,
    doi: str | None = None,
    with_document: bool = True,
) -> IngestedRecord:
    """Build a complete deterministic Step 2 record."""
    source_url, content_url = _urls(source, key)
    identifiers = () if doi is None else (IdentifierValue("doi", doi),)
    metadata = SourceMetadataRecord(
        id=f"meta_{key}",
        source_code=source,
        source_identifier=key,
        metadata_url=source_url,
        title=title,
        authors=("Ada Economist",),
        abstract="Evidence about central bank policy and macroeconomic outcomes.",
        published_at=published_at,
        asserted_updated_at=asserted_at,
        identifiers=identifiers,
        content_links=(ContentLink(content_url, "text/plain", "linked_full_text"),),
        observed_at=observed_at,
        metadata_sha256=(key.encode().hex() + _SHA_A)[:64],
        metadata_storage_uri=f"memory://metadata/{key}",
    )
    asset = AcquiredAsset(
        id=f"asset_{key}",
        source_record_id=metadata.id,
        requested_url=content_url,
        resolved_url=content_url,
        retrieved_at=observed_at,
        http_status=200,
        media_type="text/plain",
        sha256=_SHA_B,
        byte_length=len(text),
        storage_uri=f"memory://assets/{key}",
        rights_status="rights_unknown",
        license_uri=None,
    )
    unit = NormalizedUnit(
        id=f"unit_{key}",
        document_id=f"doc_{key}",
        ordinal=0,
        section_path=("Findings",),
        locator="paragraph:1",
        page_start=None,
        page_end=None,
        text=text,
    )
    document = NormalizedDocument(
        id=f"doc_{key}",
        acquired_asset_id=asset.id,
        parser_name="plain_text",
        parser_version="1.0",
        schema_version="eeg.normalized.v1",
        text_sha256=_SHA_A,
        text_storage_uri=f"memory://text/{key}",
        created_at=observed_at,
        units=(unit,),
        quality=QualityReport("plain_text", None, len(text), None, False, ()),
    )
    return IngestedRecord(
        metadata=metadata,
        assets=(asset,) if with_document else (),
        documents=(document,) if with_document else (),
        quarantines=(),
        acquisition_failures=(),
        identity_candidates=(),
        change=SourceChangeEvent(f"change_{key}", source, key, None, metadata.id, ChangeKind.NEW, observed_at),
        decisions=(),
        robots=(),
    )


def _urls(source: str, key: str) -> tuple[str, str]:
    """Return policy-valid metadata and content URLs."""
    if source == "board_fomc":
        url = f"https://www.federalreserve.gov/monetarypolicy/{key}.htm"
        return url, url
    if source == "boston_fed":
        url = f"https://www.bostonfed.org/publications/{key}"
        return url, url
    if source == "repec":
        return f"https://ideas.repec.org/p/{key}.html", f"https://publisher.example/{key}.txt"
    if source == "core":
        return f"https://api.core.ac.uk/v3/works/{key}", f"https://publisher.example/{key}.txt"
    return f"https://api.crossref.org/works/{key}", f"https://publisher.example/{key}.txt"


def _search_system(
    *records: IngestedRecord,
) -> tuple[InMemoryRepository, InMemorySearchIndex, RetrievalService, SearchIndexer]:
    """Compose the complete Step 3 in-memory pipeline."""
    repository = InMemoryRepository()
    for record in records:
        repository.put_ingested_record(record)
    embeddings = DeterministicEconomicsEmbedding(64)
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    index = InMemorySearchIndex()
    clock = FrozenClock(_NOW + timedelta(minutes=1))
    indexer = SearchIndexer(
        repository=repository,
        index=index,
        projector=SearchProjector(registry=registry, embeddings=embeddings),
        embeddings=embeddings,
        clock=clock,
    )
    retrieval = RetrievalService(
        repository=repository,
        index=index,
        embeddings=embeddings,
        policy=PolicyEngine(registry),
        clock=clock,
        ids=DeterministicIdFactory("search"),
    )
    return repository, index, retrieval, indexer


def _request(
    query: str = "monetary policy inflation",
    *,
    cutoff: datetime | None = None,
    modes: tuple[SearchMode, ...] = (SearchMode.LEXICAL, SearchMode.SEMANTIC),
    filters: SearchFilters | None = None,
    limit: int = 20,
    cursor: str | None = None,
    save: bool = False,
    collapse: bool = True,
) -> SearchRequest:
    """Build a valid application search request."""
    return SearchRequest(
        query,
        modes,
        filters or SearchFilters(),
        None if cutoff is None else HistoricalCutoff(cutoff),
        collapse,
        limit,
        cursor,
        save,
    )


def test_deterministic_embedding_is_stable_economics_aware_and_validated() -> None:
    """The local T1 vector is stable, normalized, and expands economics aliases."""
    provider = DeterministicEconomicsEmbedding(64)
    first = provider.embed("FOMC policy")
    assert first == provider.embed("FOMC policy")
    assert len(first) == provider.dimension == 64
    assert sum(value * value for value in first) == pytest.approx(1)
    assert provider.embed("fomc") != provider.embed("unrelated geology")
    assert provider.embed("")[0] == 1
    assert provider.temporal_safety_class is TemporalSafetyClass.DETERMINISTIC
    assert provider.config_id.startswith("eeg-embedding-t1-")
    with pytest.raises(ValueError, match="power of two"):
        DeterministicEconomicsEmbedding(63)


def test_availability_planner_uses_conservative_source_evidence() -> None:
    """Official, first-party, upstream, and observation dates have explicit bases."""
    board_exact = _ingested("board-exact", source="board_fomc", asserted_at=_NOW)
    board_day = _ingested("board-day", source="board_fomc")
    crossref = _ingested("crossref-updated", asserted_at=_NOW)
    observed = _ingested("observed", published_at=None)

    assert availability_decision(CorpusSearchRecord(board_exact.metadata, (), ())).basis == "official_release_timestamp"
    day = availability_decision(CorpusSearchRecord(board_day.metadata, (), ()))
    assert day.basis == "first_observed_timestamp"
    assert day.eligible_from == _NOW
    assert availability_decision(CorpusSearchRecord(crossref.metadata, (), ())).basis == "first_observed_timestamp"
    assert availability_decision(CorpusSearchRecord(observed.metadata, (), ())).basis == "first_observed_timestamp"


def test_projection_preserves_versions_exact_identity_and_suppression() -> None:
    """Only exact persistent identities collapse, and tombstones never enter search."""
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    projector = SearchProjector(registry=registry, embeddings=DeterministicEconomicsEmbedding(64))
    first = _ingested("one", doi="10.1000/shared")
    second = _ingested("two", source="core", doi="https://doi.org/10.1000/shared")
    metadata_only = _ingested("metadata-only", with_document=False)
    records = (
        CorpusSearchRecord(first.metadata, first.assets, first.documents),
        CorpusSearchRecord(second.metadata, second.assets, second.documents),
        CorpusSearchRecord(metadata_only.metadata, (), ()),
        CorpusSearchRecord(_ingested("blocked").metadata, (), (), True),
    )
    works, evidence = projector.project(records)

    assert len(works) == 3
    shared = [item for item in works if item.source_identifier in {"one", "two"}]
    assert shared[0].work_id == shared[1].work_id
    assert all(item.version_count == 2 for item in shared)
    assert len(evidence) == 2
    assert evidence[0].content_url.startswith("https://")
    assert next(item for item in works if item.source_identifier == "metadata-only").rights_statuses == (
        "metadata_only",
    )
    assert all(item.source_identifier != "blocked" for item in works)


def test_projection_consolidates_unchanged_observations_but_splits_changed_content() -> None:
    """Repeat observations do not collide in bulk, while new source bytes create a version."""
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    projector = SearchProjector(registry=registry, embeddings=DeterministicEconomicsEmbedding(64))
    first = _ingested("repeat", observed_at=datetime(2024, 1, 1, tzinfo=UTC))
    repeated = _ingested("repeat", observed_at=datetime(2024, 2, 1, tzinfo=UTC))
    repeated = replace(
        repeated,
        metadata=replace(repeated.metadata, id="meta_repeat_later"),
        assets=(replace(repeated.assets[0], id="asset_repeat_later", source_record_id="meta_repeat_later"),),
        documents=(
            replace(
                repeated.documents[0],
                id="doc_repeat_later",
                acquired_asset_id="asset_repeat_later",
                units=(
                    replace(
                        repeated.documents[0].units[0],
                        id="unit_repeat_later",
                        document_id="doc_repeat_later",
                    ),
                ),
            ),
        ),
    )
    changed = _ingested("repeat", observed_at=datetime(2024, 3, 1, tzinfo=UTC))
    changed = replace(changed, assets=(replace(changed.assets[0], sha256="c" * 64),))
    records = tuple(
        CorpusSearchRecord(item.metadata, item.assets, item.documents) for item in (first, repeated, changed)
    )
    works, evidence = projector.project(records)
    assert len(works) == 2
    assert len(evidence) == 2
    assert all(item.version_count == 2 for item in works)


def test_evidence_bytes_are_never_backdated_to_metadata_release() -> None:
    """Later-retrieved content cannot enter an earlier strict historical evidence channel."""
    release = datetime(2025, 1, 1, tzinfo=UTC)
    retrieved = datetime(2026, 1, 1, tzinfo=UTC)
    item = _ingested("official", source="board_fomc", observed_at=retrieved, asserted_at=release)
    works, evidence = SearchProjector(
        registry=SourceRegistry.load(_ROOT / "config/source-registry.toml"),
        embeddings=DeterministicEconomicsEmbedding(64),
    ).project((CorpusSearchRecord(item.metadata, item.assets, item.documents),))
    assert works[0].eligible_from == release
    assert evidence[0].eligible_from == retrieved
    assert evidence[0].date_basis == "content_first_observed_timestamp"


def test_repec_projection_identity_and_invalid_corpus_lineage() -> None:
    """RePEc handles form stable work identities and source/document lineage is checked."""
    item = _ingested("RePEc:fip:fedbwp:24-1", source="repec")
    projector = SearchProjector(
        registry=SourceRegistry.load(_ROOT / "config/source-registry.toml"),
        embeddings=DeterministicEconomicsEmbedding(64),
    )
    works, _ = projector.project((CorpusSearchRecord(item.metadata, item.assets, item.documents),))
    assert works[0].work_id.startswith("wrk_")
    with pytest.raises(EconomicEvidenceGraphError, match="asset lineage"):
        CorpusSearchRecord(replace(item.metadata, id="other"), item.assets, item.documents)
    with pytest.raises(EconomicEvidenceGraphError, match="document lineage"):
        CorpusSearchRecord(item.metadata, (), item.documents)


def test_indexer_builds_idempotent_generation_and_memory_channels() -> None:
    """A rebuild activates an immutable generation and all channels honor filters."""
    repository, index, _, indexer = _search_system(
        _ingested("old", observed_at=datetime(2020, 1, 1, tzinfo=UTC)),
        _ingested("new", title="Labor market slack", text="Employment and unemployment evidence."),
    )
    generation = indexer.rebuild()
    repeated = indexer.rebuild()
    assert generation == repeated == repository.latest_index_generation()
    assert generation.work_document_count == 2
    assert generation.evidence_document_count == 2
    assert index.active_generation == generation
    filters = SearchIndexFilters(source_codes=("crossref",))
    lexical = index.search(
        index_kind="work", channel="lexical", query="monetary", query_vector=None, filters=filters, limit=10
    )
    semantic = index.search(
        index_kind="evidence",
        channel="semantic",
        query="employment",
        query_vector=DeterministicEconomicsEmbedding(64).embed("employment"),
        filters=filters,
        limit=10,
    )
    structured = index.search(
        index_kind="work", channel="structured", query="x", query_vector=None, filters=filters, limit=10
    )
    assert lexical[0].document["source_identifier"] == "old"
    assert semantic
    assert len(structured) == 2
    assert index.get_evidence(cast(str, semantic[0].document["id"])) is not None
    index.check()
    index.close()


def test_memory_index_filters_errors_and_dimension_guard() -> None:
    """The contract provider rejects invalid calls and filters before ranking."""
    record = _ingested("filter")
    _, index, _, indexer = _search_system(record)
    generation = indexer.rebuild()
    empty = index.search(
        index_kind="work",
        channel="lexical",
        query="monetary",
        query_vector=None,
        filters=SearchIndexFilters(source_codes=("core",)),
        limit=5,
    )
    assert empty == ()
    assert (
        index.search(
            index_kind="work",
            channel="structured",
            query="x",
            query_vector=None,
            filters=SearchIndexFilters(published_from=date(2025, 1, 1)),
            limit=5,
        )
        == ()
    )
    with pytest.raises(SearchBackendError, match="query vector"):
        index.search(
            index_kind="work",
            channel="semantic",
            query="x",
            query_vector=None,
            filters=SearchIndexFilters(),
            limit=1,
        )
    with pytest.raises(SearchBackendError, match="channel"):
        index.search(
            index_kind="work", channel="bad", query="x", query_vector=None, filters=SearchIndexFilters(), limit=1
        )
    with pytest.raises(SearchBackendError, match="kind"):
        index.search(
            index_kind="bad", channel="lexical", query="x", query_vector=None, filters=SearchIndexFilters(), limit=1
        )
    projected_works, _ = indexer._projector.project(
        (CorpusSearchRecord(record.metadata, record.assets, record.documents),)
    )
    projected = replace(projected_works[0], embedding=(1.0,))
    with pytest.raises(SearchBackendError, match="dimension"):
        index.replace_generation(generation, (projected,), (), embedding_dimension=64)


def test_hybrid_retrieval_is_explainable_saved_and_paginated() -> None:
    """Fusion returns bounded evidence, per-channel ranks, saved runs, and bound cursors."""
    repository, _, retrieval, indexer = _search_system(
        _ingested("one"),
        _ingested("two", title="Inflation dynamics under monetary policy"),
        _ingested("three", title="Monetary transmission mechanisms"),
    )
    indexer.rebuild()
    first = retrieval.search(_request(limit=1, save=True, modes=(SearchMode.LEXICAL, SearchMode.SEMANTIC)))
    assert len(first.results) == 1
    assert first.results[0].evidence is not None
    assert len(cast(str, first.results[0].evidence.text)) <= 500
    assert {item.channel for item in first.results[0].contributions} >= {"lexical_work", "semantic_work"}
    assert len({item.channel for item in first.results[0].contributions}) == len(first.results[0].contributions)
    assert first.next_cursor is not None
    assert first.search_run_id is not None
    saved = retrieval.saved_run(first.search_run_id)
    assert saved.response_payload["index_generation_id"] == first.index_generation_id
    second = retrieval.search(_request(limit=1, cursor=first.next_cursor))
    assert second.results[0].rank == 2
    assert second.results[0].document_version_id != first.results[0].document_version_id
    assert repository.get_search_run(first.search_run_id) == saved
    with pytest.raises(NotFoundError):
        retrieval.saved_run("missing")


def test_strict_cutoff_filters_before_all_channels_and_cursor_is_bound() -> None:
    """Post-cutoff documents cannot leak through lexical, vector, or structured candidate sets."""
    cutoff = datetime(2020, 6, 1, tzinfo=UTC)
    _, _, retrieval, indexer = _search_system(
        _ingested("eligible", observed_at=datetime(2020, 1, 1, tzinfo=UTC), published_at=None),
        _ingested("future", observed_at=datetime(2021, 1, 1, tzinfo=UTC), published_at=None),
    )
    indexer.rebuild()
    response = retrieval.search(
        _request(
            cutoff=cutoff,
            modes=(SearchMode.LEXICAL, SearchMode.SEMANTIC, SearchMode.STRUCTURED),
            filters=SearchFilters(source_codes=("crossref",)),
            limit=1,
        )
    )
    assert [item.source_identifier for item in response.results] == ["eligible"]
    assert response.results[0].temporal.eligible
    assert "structured_work" in response.modes_used
    unfiltered = retrieval.search(_request(modes=(SearchMode.STRUCTURED,)))
    assert unfiltered.results == ()
    assert unfiltered.omitted_channels == ("structured:no_filters",)
    with pytest.raises(ConflictError, match="cursor"):
        retrieval.search(_request(query="different", cursor="eyJvZmZzZXQiOjF9"))
    with pytest.raises(ConflictError, match="invalid"):
        retrieval.search(_request(cursor="!"))


def test_strict_search_omits_uncertified_learned_embeddings() -> None:
    """A future T2 provider cannot introduce computational hindsight by default."""

    class LearnedEmbedding(DeterministicEconomicsEmbedding):
        @property
        def temporal_safety_class(self) -> TemporalSafetyClass:
            return TemporalSafetyClass.LEARNED

    repository, index, _, indexer = _search_system(_ingested("learned"))
    indexer.rebuild()
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    retrieval = RetrievalService(
        repository=repository,
        index=index,
        embeddings=LearnedEmbedding(64),
        policy=PolicyEngine(registry),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("learned"),
    )
    response = retrieval.search(_request(cutoff=_NOW, modes=(SearchMode.LEXICAL, SearchMode.SEMANTIC)))
    assert response.results
    assert response.omitted_channels == ("semantic:T2_not_certified_for_strict",)


def test_retrieval_requires_an_active_generation() -> None:
    """Search cannot silently operate without a recorded corpus generation."""
    _, _, retrieval, _ = _search_system(_ingested("not-indexed"))
    with pytest.raises(ConflictError, match="has not been built"):
        retrieval.search(_request())


def test_version_collapse_and_policy_denial_behavior() -> None:
    """Exact DOI versions collapse after fusion while denied excerpts become metadata-only results."""
    _, index, retrieval, indexer = _search_system(
        _ingested("v1", doi="10.1000/same"),
        _ingested("v2", source="core", doi="10.1000/same"),
    )
    indexer.rebuild()
    collapsed = retrieval.search(_request())
    expanded = retrieval.search(_request(collapse=False))
    assert len(collapsed.results) == 1
    assert collapsed.results[0].alternative_version_count == 1
    assert len(expanded.results) == 2
    evidence_id = cast(str, next(iter(index._evidence.values()))["id"])
    assert retrieval.evidence(evidence_id).evidence_unit_id == evidence_id
    with pytest.raises(NotFoundError):
        retrieval.evidence("missing")
    index._evidence[evidence_id]["content_url"] = "ftp://evil.example/paper"
    with pytest.raises(PolicyDeniedError):
        retrieval.evidence(evidence_id)
    withheld = retrieval.search(_request())
    assert withheld.results[0].evidence is None
    assert "withheld" in withheld.results[0].warnings[-1]


def test_post_index_suppression_immediately_blocks_search_and_reader() -> None:
    """A takedown tombstone gates stale index hits without waiting for rebuild."""
    repository, index, retrieval, indexer = _search_system(_ingested("takedown"))
    indexer.rebuild()
    evidence_id = cast(str, next(iter(index._evidence.values()))["id"])
    repository.put_suppression(
        SuppressionRecord(
            "sup_takedown",
            "source_record",
            "crossref:takedown",
            "test takedown",
            _NOW,
            False,
        )
    )
    assert repository.list_suppression_scopes() == (("source_record", "crossref:takedown"),)
    assert retrieval.search(_request()).results == ()
    with pytest.raises(PolicyDeniedError, match="suppressed"):
        retrieval.evidence(evidence_id)


def test_search_domain_guards_invalid_requests_and_records() -> None:
    """Search contracts fail early on malformed dates, ranges, scores, and hashes."""
    with pytest.raises(EconomicEvidenceGraphError, match="range"):
        SearchFilters(published_from=date(2025, 1, 1), published_to=date(2024, 1, 1))
    with pytest.raises(EconomicEvidenceGraphError, match="1 to 500"):
        _request(query="")
    with pytest.raises(EconomicEvidenceGraphError, match="unique"):
        _request(modes=(SearchMode.LEXICAL, SearchMode.LEXICAL))
    with pytest.raises(EconomicEvidenceGraphError, match="between"):
        _request(limit=51)
    with pytest.raises(EconomicEvidenceGraphError, match="rank"):
        ChannelContribution("x", 0, 1, 1)
    with pytest.raises(EconomicEvidenceGraphError, match="SHA-256"):
        IndexGeneration("x", "m", "r", "e", "s", "bad", "w", "e", 0, 0, _NOW, _NOW)
    with pytest.raises(EconomicEvidenceGraphError, match="query hash"):
        SearchRun("x", {}, {}, "g", "bad", _NOW)
    with pytest.raises(EconomicEvidenceGraphError, match="UTC"):
        AvailabilityDecision(datetime(2020, 1, 1), "basis", "evidence", None)
    with pytest.raises(EconomicEvidenceGraphError, match="basis"):
        AvailabilityDecision(_NOW, "", "", None)


class _OpenSearchState:
    """Stateful HTTP handler implementing the subset used by the adapter."""

    def __init__(self) -> None:
        self.indexes: dict[str, dict[str, dict[str, object]]] = {}
        self.aliases: dict[str, str] = {}
        self.bad_json = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.bad_json:
            return httpx.Response(200, text="not-json", request=request)
        if path == "/_cluster/health":
            return httpx.Response(200, json={"status": "green"}, request=request)
        if path == "/_bulk":
            lines = request.content.decode().splitlines()
            for position in range(0, len(lines), 2):
                action = json.loads(lines[position])["index"]
                self.indexes[action["_index"]][action["_id"]] = json.loads(lines[position + 1])
            return httpx.Response(200, json={"errors": False}, request=request)
        if path == "/_aliases":
            for action in cast(dict[str, list[dict[str, dict[str, str]]]], json.loads(request.content))["actions"]:
                operation, value = next(iter(action.items()))
                if operation == "remove":
                    self.aliases.pop(value["alias"], None)
                else:
                    self.aliases[value["alias"]] = value["index"]
            return httpx.Response(200, json={"acknowledged": True}, request=request)
        if path.startswith("/_alias/"):
            alias = path.rsplit("/", 1)[1]
            if alias not in self.aliases:
                return httpx.Response(404, json={}, request=request)
            return httpx.Response(200, json={self.aliases[alias]: {"aliases": {alias: {}}}}, request=request)
        parts = path.strip("/").split("/")
        name = parts[0]
        resolved = self.aliases.get(name, name)
        if request.method == "HEAD":
            return httpx.Response(200 if resolved in self.indexes else 404, request=request)
        if request.method == "PUT" and len(parts) == 1:
            self.indexes[resolved] = {}
            return httpx.Response(200, json={"acknowledged": True}, request=request)
        if parts[-1] == "_refresh":
            return httpx.Response(200, json={"_shards": {}}, request=request)
        if parts[-1] == "_count":
            return httpx.Response(200, json={"count": len(self.indexes[resolved])}, request=request)
        if len(parts) >= 3 and parts[-2] == "_doc":
            item = self.indexes.get(resolved, {}).get(parts[-1])
            return httpx.Response(
                404 if item is None else 200,
                json={} if item is None else {"_source": item},
                request=request,
            )
        if parts[-1] == "_search":
            documents = list(self.indexes[resolved].values())
            hits = [{"_score": 2.0, "_source": item} for item in documents[:2]]
            return httpx.Response(200, json={"hits": {"hits": hits}}, request=request)
        return httpx.Response(500, json={"error": "unhandled"}, request=request)


def test_opensearch_adapter_builds_aliases_and_runs_every_query_shape() -> None:
    """The real adapter creates strict generations, bulk loads, aliases, and reads hits."""
    record = _ingested("open-search")
    _, _, _, indexer = _search_system(record)
    generation = indexer.rebuild()
    works, evidence = indexer._projector.project(
        (CorpusSearchRecord(record.metadata, record.assets, record.documents),)
    )
    state = _OpenSearchState()
    client = httpx.Client(transport=httpx.MockTransport(state), base_url="http://search")
    index = OpenSearchIndex("http://unused", bulk_batch_size=1, client=client)
    index.replace_generation(generation, works, evidence, embedding_dimension=64)
    index.replace_generation(generation, works, evidence, embedding_dimension=64)
    index.check()
    common = {
        "query": "policy",
        "filters": SearchIndexFilters(
            source_codes=("crossref",),
            published_from=date(2020, 1, 1),
            published_to=date(2025, 1, 1),
            eligible_at=_NOW,
            allowed_safety_classes=("T0", "T1"),
        ),
        "limit": 5,
    }
    assert index.search(index_kind="work", channel="lexical", query_vector=None, **common)
    assert index.search(
        index_kind="evidence",
        channel="semantic",
        query_vector=DeterministicEconomicsEmbedding(64).embed("policy"),
        **common,
    )
    assert index.search(index_kind="work", channel="structured", query_vector=None, **common)
    evidence_id = evidence[0].id
    assert index.get_evidence(evidence_id) is not None
    assert index.get_evidence("missing") is None
    assert index.get_work_versions(works[0].work_id, limit=5)
    assert index.list_version_evidence(evidence[0].document_version_id, limit=5)
    index.close()
    client.close()


def test_opensearch_adapter_normalizes_backend_failures() -> None:
    """Malformed responses, bad channels, missing vectors, and count drift fail closed."""
    state = _OpenSearchState()
    client = httpx.Client(transport=httpx.MockTransport(state), base_url="http://search")
    index = OpenSearchIndex("http://unused", client=client)
    with pytest.raises(ValueError, match="batch size"):
        OpenSearchIndex("http://unused", bulk_batch_size=0, client=client)
    with pytest.raises(SearchBackendError, match="kind"):
        index.search(
            index_kind="bad", channel="lexical", query="x", query_vector=None, filters=SearchIndexFilters(), limit=1
        )
    with pytest.raises(SearchBackendError, match="query vector"):
        index.search(
            index_kind="work", channel="semantic", query="x", query_vector=None, filters=SearchIndexFilters(), limit=1
        )
    with pytest.raises(SearchBackendError, match="channel"):
        index.search(
            index_kind="work", channel="bad", query="x", query_vector=None, filters=SearchIndexFilters(), limit=1
        )
    state.bad_json = True
    with pytest.raises(SearchBackendError, match="invalid JSON"):
        index.check()
    state.bad_json = False
    state.indexes["existing"] = {}
    response = httpx.Response(500, json={"error": "no"}, request=httpx.Request("GET", "http://search/fail"))
    with pytest.raises(SearchBackendError, match="rejected"):
        OpenSearchIndex._response(response)
    non_object = httpx.Response(200, json=[], request=httpx.Request("GET", "http://search/list"))
    with pytest.raises(SearchBackendError, match="non-object"):
        OpenSearchIndex._response(non_object)
    client.close()

    red_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "red"}, request=request)),
        base_url="http://search",
    )
    with pytest.raises(SearchBackendError, match="status is red"):
        OpenSearchIndex("http://unused", client=red_client).check()
    red_client.close()


def test_sql_search_projection_generation_and_run_round_trip() -> None:
    """The PostgreSQL contract persists joined corpus projection and immutable run manifests."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    sql_metadata.create_all(engine)
    repository = SqlRepository(engine)
    item = _ingested("sql-search")
    repository.put_ingested_record(item)
    records = repository.list_corpus_search_records()
    assert records[0].documents[0].units[0].text.startswith("Forward guidance")
    _, index, _, indexer = _search_system()
    sql_indexer = SearchIndexer(
        repository=repository,
        index=index,
        projector=indexer._projector,
        embeddings=indexer._embeddings,
        clock=FrozenClock(_NOW),
    )
    generation = sql_indexer.rebuild()
    assert repository.get_index_generation(generation.id) == generation
    sql_retrieval = RetrievalService(
        repository=repository,
        index=index,
        embeddings=indexer._embeddings,
        policy=PolicyEngine(SourceRegistry.load(_ROOT / "config/source-registry.toml")),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("sql-search"),
    )
    response = sql_retrieval.search(_request(save=True))
    assert response.search_run_id is not None
    assert repository.get_search_run(response.search_run_id) is not None
    repository.close()


def test_search_api_workbench_search_evidence_and_replay(memory_system: object) -> None:
    """The local workbench and all Step 3 HTTP contracts operate over one generation."""
    repository = memory_system.repository  # type: ignore[attr-defined]
    repository.put_ingested_record(_ingested("api-search"))
    _, index, retrieval, indexer = _search_system(_ingested("api-search"))
    indexer.rebuild()
    container: Container = replace(
        memory_system.container,  # type: ignore[attr-defined]
        retrieval=retrieval,
        indexer=indexer,
        search_index=index,
    )
    client = ApiClient(create_app(container))
    assert "Economic Evidence Graph" in client.get("/").text
    body = {
        "query": "monetary policy",
        "modes": ["lexical", "semantic"],
        "historical_cutoff": {"at": "2026-08-04T20:00:00Z", "mode": "strict"},
        "save_run": True,
    }
    response = client.post("/v1/search", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "eeg.api.v1"
    run_id = payload["search_run_id"]
    assert client.get(f"/v1/search/runs/{run_id}").status_code == 200
    replay = client.post(f"/v1/search/runs/{run_id}/replay", json={})
    assert replay.json()["index_generation_id"] == payload["index_generation_id"]
    evidence_id = payload["results"][0]["evidence"]["evidence_unit_id"]
    assert client.get(f"/v1/evidence/{evidence_id}").json()["content_sha256"] == _SHA_B
    assert client.get("/health/ready").status_code == 200


def test_search_api_requires_configured_service(memory_system: object) -> None:
    """Step 1-only containers return a structured configuration error for search."""
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    response = client.post("/v1/search", json={"query": "policy"})
    assert response.status_code == 400
    assert response.json()["code"] == "configuration_error"


def test_cli_search_rebuild_and_timestamp_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator commands expose index activation and strict hybrid search."""
    _, index, retrieval, indexer = _search_system(_ingested("cli-search"))
    container = cast(Any, type("SearchContainer", (), {})())
    container.indexer = indexer
    container.retrieval = retrieval
    container.close = lambda: None
    settings = cast(Any, type("Settings", (), {"database_url": "postgresql+psycopg://local"})())
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "upgrade", lambda _url: None)
    monkeypatch.setattr(cli, "create_local_container", lambda _settings: container)
    assert cli.main(["rebuild-search"]) == 0
    assert json.loads(capsys.readouterr().out)["work_document_count"] == 1
    assert cli.main(["search", "monetary policy", "--as-of", "2026-08-04T20:00:00Z", "--save-run"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["source_identifier"] == "cli-search"
    with pytest.raises(SystemExit):
        cli.main(["search", "x", "--as-of", "2020-01-01"])
    assert index.active_generation is not None
