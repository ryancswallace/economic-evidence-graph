"""Step 5 Python/R clients, agent tools, audit, and local readiness tests."""

from __future__ import annotations

import json
from asyncio import run
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine

from economic_evidence_graph import cli
from economic_evidence_graph.api import create_app
from economic_evidence_graph.api.project_schemas import ProjectCreateRequest
from economic_evidence_graph.api.schemas import SearchCreateRequest
from economic_evidence_graph.application import (
    AgentToolService,
    CatalogService,
    EvidenceAnchorInput,
    EvidenceFieldInput,
    EvidencePackExporter,
    JobService,
    LocalReadinessService,
    ProjectService,
    PromotionManifestService,
    RetrievalService,
    TableCellInput,
)
from economic_evidence_graph.bootstrap import Container
from economic_evidence_graph.client import EegClient, EegClientError
from economic_evidence_graph.config import Settings
from economic_evidence_graph.domain import (
    AgentToolName,
    EvidenceLineageMethod,
    EvidenceState,
    EvidenceTableColumn,
    IndexGeneration,
    PolicyLinkType,
    ProjectExportFormat,
    ProjectRole,
    SearchEvidenceDocument,
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchWorkDocument,
    TemporalSafetyClass,
    ToolInvocationOutcome,
    ToolInvocationRecord,
)
from economic_evidence_graph.exceptions import AuthorizationError, EconomicEvidenceGraphError, NotFoundError
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.providers import (
    DeterministicEconomicsEmbedding,
    DeterministicIdFactory,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryRepository,
    InMemorySearchIndex,
)
from economic_evidence_graph.providers.postgres import SqlRepository
from economic_evidence_graph.providers.postgres.schema import metadata as sql_metadata
from economic_evidence_graph.serialization import record_sha256, to_json_value

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 6, 1, 0, tzinfo=UTC)
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


@dataclass(frozen=True, slots=True)
class ToolSystem:
    """Complete in-memory Step 5 contract system."""

    repository: InMemoryRepository
    artifacts: InMemoryArtifactStore
    index: InMemorySearchIndex
    retrieval: RetrievalService
    projects: ProjectService
    tools: AgentToolService
    container: Container
    project_id: str
    table_id: str
    project_revision: int


class ApiClient:
    """Small synchronous ASGI client supporting actor headers."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str, *, actor: str = "owner") -> httpx.Response:
        """Issue an actor-attributed GET."""
        return run(self._request("GET", path, headers={"X-EEG-Actor": actor}))

    def post(self, path: str, *, json: object, actor: str = "owner") -> httpx.Response:
        """Issue an actor-attributed POST."""
        return run(self._request("POST", path, json=json, headers={"X-EEG-Actor": actor}))

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Call the public ASGI interface."""
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)


def _work_document(
    *,
    identifier: str,
    work_id: str,
    version_id: str,
    title: str,
    text: str,
) -> SearchWorkDocument:
    """Build one complete work-version projection."""
    embeddings = DeterministicEconomicsEmbedding(64)
    return SearchWorkDocument(
        id=identifier,
        work_id=work_id,
        document_version_id=version_id,
        source_record_id=f"meta_{identifier}",
        source_code="crossref",
        source_identifier=f"10.0000/{identifier}",
        source_url=f"https://api.crossref.org/works/{identifier}",
        title=title,
        authors=("Ada Economist",),
        abstract=text,
        identifiers=(f"doi:10.0000/{identifier}",),
        published_at=date(2024, 1, 1),
        observed_at=_NOW,
        eligible_from=_NOW,
        date_basis="system_first_observed",
        date_evidence="test observation",
        date_uncertainty=None,
        temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
        policy_version=1,
        normalized_document_ids=(f"doc_{identifier}",),
        asset_ids=(f"asset_{identifier}",),
        rights_statuses=("rights_unknown",),
        quality_warnings=(),
        version_count=2 if work_id == "work_research" else 1,
        embedding=embeddings.embed(f"{title} {text}"),
    )


def _evidence_document(
    *,
    identifier: str,
    work_id: str,
    version_id: str,
    text: str,
    text_sha256: str,
    source_code: str = "crossref",
    locator: str = "paragraph:1",
) -> SearchEvidenceDocument:
    """Build one complete evidence projection."""
    embeddings = DeterministicEconomicsEmbedding(64)
    if source_code == "crossref":
        url = f"https://api.crossref.org/works/{identifier}"
    else:
        url = f"https://www.federalreserve.gov/monetarypolicy/{identifier}.htm"
    return SearchEvidenceDocument(
        id=identifier,
        work_id=work_id,
        document_version_id=version_id,
        source_record_id=f"meta_{identifier}",
        normalized_document_id=f"doc_{identifier}",
        acquired_asset_id=f"asset_{identifier}",
        source_code=source_code,
        source_identifier=identifier,
        source_url=url,
        content_url=url,
        title=f"Title for {work_id}",
        authors=("Ada Economist",),
        published_at=date(2024, 1, 1),
        observed_at=_NOW,
        eligible_from=_NOW,
        date_basis="system_first_observed",
        date_evidence="test observation",
        date_uncertainty=None,
        temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
        policy_version=1,
        text=text,
        section_path=("Findings",),
        locator=locator,
        page_start=1,
        page_end=1,
        parser_name="plain_text",
        parser_version="1.0",
        extraction_schema_version="eeg.normalized.v1",
        text_sha256=text_sha256,
        content_sha256=_SHA_C,
        retrieved_at=_NOW,
        rights_status="rights_unknown",
        quality_warnings=(),
        version_count=2 if work_id == "work_research" else 1,
        embedding=embeddings.embed(text),
    )


def _tool_system() -> ToolSystem:
    """Compose all eight tools over project, search, audit, and export services."""
    repository = InMemoryRepository()
    artifacts = InMemoryArtifactStore()
    index = InMemorySearchIndex()
    embeddings = DeterministicEconomicsEmbedding(64)
    works = (
        _work_document(
            identifier="work_left",
            work_id="work_research",
            version_id="version_left",
            title="Policy Transmission",
            text="Earlier output estimate.",
        ),
        _work_document(
            identifier="work_right",
            work_id="work_research",
            version_id="version_right",
            title="Policy Transmission",
            text="Revised output estimate.",
        ),
        _work_document(
            identifier="work_policy_projection",
            work_id="work_policy",
            version_id="version_policy",
            title="Policy Deliberation",
            text="Policy discussion citing research.",
        ),
    )
    evidence = (
        _evidence_document(
            identifier="evidence_left",
            work_id="work_research",
            version_id="version_left",
            text="The estimated output response is minus 1.0 percent after four quarters.",
            text_sha256=_SHA_A,
        ),
        _evidence_document(
            identifier="evidence_right",
            work_id="work_research",
            version_id="version_right",
            text="The estimated output response is minus 1.5 percent after four quarters.",
            text_sha256=_SHA_B,
        ),
        _evidence_document(
            identifier="evidence_left_only",
            work_id="work_research",
            version_id="version_left",
            text="This appendix appeared only in the earlier version.",
            text_sha256=_SHA_A,
            locator="paragraph:2",
        ),
        _evidence_document(
            identifier="evidence_right_only",
            work_id="work_research",
            version_id="version_right",
            text="This robustness section appeared only in the revised version.",
            text_sha256=_SHA_B,
            locator="paragraph:3",
        ),
        _evidence_document(
            identifier="evidence_policy",
            work_id="work_policy",
            version_id="version_policy",
            text="The Committee directly cited the research estimate in its policy discussion.",
            text_sha256=_SHA_C,
            source_code="board_fomc",
        ),
    )
    generation = IndexGeneration(
        id="idxgen_step5",
        mapping_version="eeg-search-v1",
        retrieval_config_id="eeg-retrieval-v1",
        embedding_config_id=embeddings.config_id,
        corpus_snapshot_id="snapshot_step5",
        manifest_sha256=_SHA_A,
        work_index_name="work-step5",
        evidence_index_name="evidence-step5",
        work_document_count=len(works),
        evidence_document_count=len(evidence),
        created_at=_NOW,
        activated_at=_NOW,
    )
    index.replace_generation(generation, works, evidence, embedding_dimension=64)
    repository.put_index_generation(generation)
    retrieval = RetrievalService(
        repository=repository,
        index=index,
        embeddings=embeddings,
        policy=PolicyEngine(SourceRegistry.load(_ROOT / "config/source-registry.toml")),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("step5-search"),
    )
    projects = ProjectService(
        repository=repository,
        search_repository=repository,
        search_index=index,
        retrieval=retrieval,
        artifacts=artifacts,
        clock=FrozenClock(_NOW),
    )
    project = projects.create(
        name="Step 5 tools",
        description="Client and tool contract fixture",
        actor_id="owner",
        idempotency_key="create-step5",
    )
    project = projects.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="viewer",
        role=ProjectRole.VIEWER,
        expected_revision=project.revision,
        idempotency_key="add-viewer",
    )
    project = projects.add_evidence_record(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_right",
        record_type="estimate",
        fields=(
            EvidenceFieldInput(
                field_name="estimate",
                display_value="-1.5",
                normalized_value=-1.5,
                units="percent",
                applicability="four-quarter response",
                missingness_reason=None,
                state=EvidenceState.OBSERVED,
                lineage_method=EvidenceLineageMethod.OBSERVED_TRANSCRIPTION,
                confidence=1,
                anchors=(EvidenceAnchorInput("evidence_right", "output response is minus 1.5 percent"),),
            ),
        ),
        expected_revision=project.revision,
        idempotency_key="record-step5",
    )
    bundle = projects.get(project.id, actor_id="owner")
    record = bundle.evidence_records[0]
    project = projects.create_evidence_table(
        project.id,
        actor_id="owner",
        name="Estimate comparison",
        description="Agent-tool fixture",
        columns=(EvidenceTableColumn("estimate", "Estimate", "number", "percent"),),
        expected_revision=project.revision,
        idempotency_key="table-step5",
    )
    table_id = projects.get(project.id, actor_id="owner").evidence_tables[0].id
    project = projects.add_evidence_table_row(
        project.id,
        table_id,
        actor_id="owner",
        evidence_record_id=record.id,
        label="Revised estimate",
        cells=(TableCellInput("estimate", record.fields[0].id),),
        expected_revision=project.revision,
        idempotency_key="row-step5",
    )
    project = projects.add_policy_link(
        project.id,
        actor_id="owner",
        research_work_id="work_research",
        policy_work_id="work_policy",
        relationship_type=PolicyLinkType.DIRECT_CITATION,
        anchor=EvidenceAnchorInput("evidence_policy", "directly cited the research estimate"),
        method="manual_review",
        confidence=1,
        state=EvidenceState.OBSERVED,
        expected_revision=project.revision,
        idempotency_key="link-step5",
    )
    clock = FrozenClock(_NOW)
    tools = AgentToolService(
        retrieval=retrieval,
        projects=projects,
        audit=repository,
        clock=clock,
        ids=DeterministicIdFactory("step5-tools"),
    )
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
            ids=DeterministicIdFactory("step5-jobs"),
        ),
        retrieval=retrieval,
        search_index=index,
        projects=projects,
        tools=tools,
    )
    return ToolSystem(
        repository,
        artifacts,
        index,
        retrieval,
        projects,
        tools,
        container,
        project.id,
        table_id,
        project.revision,
    )


def test_all_eight_tools_are_bounded_provenance_complete_and_audited() -> None:
    """Every declared tool runs through shared services and privacy-safe audit."""
    system = _tool_system()
    search = system.tools.search_literature(
        SearchRequest(
            query="output response",
            modes=(SearchMode.LEXICAL,),
            filters=SearchFilters(),
            historical_cutoff=None,
            collapse_versions=True,
            limit=5,
        ),
        actor_id="owner",
    )
    assert search.result["index_generation_id"] == "idxgen_step5"
    work = system.tools.get_work("work_research", actor_id="owner")
    assert len(cast(list[object], work.result["versions"])) == 2
    evidence = system.tools.retrieve_evidence("evidence_right", actor_id="owner")
    assert cast(dict[str, Any], evidence.result["evidence"])["locator"] == "paragraph:1"
    comparison = system.tools.compare_evidence(system.project_id, system.table_id, actor_id="owner")
    rows = cast(list[dict[str, Any]], comparison.result["rows"])
    assert rows[0]["values"]["estimate"]["display_value"] == "-1.5"
    versions = system.tools.compare_versions(
        "work_research",
        "version_left",
        "version_right",
        actor_id="owner",
    )
    assert [item["kind"] for item in cast(list[dict[str, Any]], versions.result["changes"])] == [
        "changed",
        "removed",
        "added",
    ]
    citations = system.tools.trace_citations(system.project_id, "work_research", actor_id="owner")
    assert cast(list[dict[str, Any]], citations.result["edges"])[0]["relationship_type"] == "direct_citation"
    policy = system.tools.trace_policy_links(system.project_id, "work_policy", actor_id="owner")
    assert policy.result["nodes"] == ["work_policy", "work_research"]
    exported = system.tools.export_project(
        system.project_id,
        system.table_id,
        actor_id="owner",
        format=ProjectExportFormat.JSON,
        expected_revision=system.project_revision,
        idempotency_key="tool-export-step5",
    )
    assert exported.result["manifest_sha256"]
    audits = system.repository.list_tool_invocations()
    assert len(audits) == 8
    assert {item.tool_name for item in audits} == set(AgentToolName)
    assert all(item.outcome is ToolInvocationOutcome.SUCCESS and item.result_sha256 for item in audits)
    assert "output response" not in json.dumps(to_json_value(audits))


def test_tool_denials_errors_limits_and_version_modes_are_explicitly_audited() -> None:
    """Tool misuse fails closed while useful terminal audit remains content-minimized."""
    system = _tool_system()
    with pytest.raises(AuthorizationError):
        system.tools.compare_evidence(system.project_id, system.table_id, actor_id="intruder")
    with pytest.raises(NotFoundError):
        system.tools.get_work("missing_work", actor_id="owner")
    with pytest.raises(ValueError, match="limit"):
        system.tools.compare_versions(
            "work_research",
            "version_left",
            "version_right",
            actor_id="owner",
            limit=101,
        )
    with pytest.raises(ValueError, match="agent search limit"):
        system.tools.search_literature(
            SearchRequest(
                query="inflation",
                modes=(SearchMode.LEXICAL,),
                filters=SearchFilters(),
                historical_cutoff=None,
                collapse_versions=True,
                limit=21,
            ),
            actor_id="owner",
        )
    with pytest.raises(NotFoundError, match="both versions"):
        system.tools.compare_versions(
            "work_research",
            "version_left",
            "missing_version",
            actor_id="owner",
        )
    unchanged = system.tools.compare_versions(
        "work_research",
        "version_left",
        "version_left",
        actor_id="owner",
        include_unchanged=True,
        limit=1,
    )
    assert cast(list[dict[str, Any]], unchanged.result["changes"])[0]["kind"] == "unchanged"
    with pytest.raises(ValueError, match="depth"):
        system.tools.trace_policy_links(system.project_id, "work_research", actor_id="owner", depth=4)
    with pytest.raises(ValueError, match="node or edge"):
        system.tools.trace_policy_links(system.project_id, "work_research", actor_id="owner", node_limit=0)
    truncated = system.tools.trace_policy_links(
        system.project_id,
        "work_research",
        actor_id="owner",
        node_limit=1,
    )
    assert truncated.result["truncated"] is True
    with pytest.raises(ValueError, match="actor"):
        system.tools.get_work("work_research", actor_id="")
    outcomes = [item.outcome for item in system.repository.list_tool_invocations()]
    assert {item.value for item in outcomes} == {"denied", "error", "success"}


def test_agent_tool_http_contract_executes_all_tools_and_rejects_oversized_inputs() -> None:
    """OpenAPI routes retain one schema per tool and enforce interface bounds."""
    system = _tool_system()
    app = create_app(system.container)
    client = ApiClient(app)
    checked_contract = json.loads((_ROOT / "clients/openapi/eeg-v1.json").read_text(encoding="utf-8"))
    assert app.openapi() == checked_contract
    definitions = client.get("/v1/agent-tools").json()
    assert [item["name"] for item in definitions] == [item.value for item in AgentToolName]
    calls = (
        ("search_literature", {"query": "output response", "modes": ["lexical"], "limit": 5}),
        ("get_work", {"work_id": "work_research"}),
        ("retrieve_evidence", {"evidence_unit_id": "evidence_right"}),
        ("compare_evidence", {"project_id": system.project_id, "table_id": system.table_id}),
        (
            "compare_versions",
            {
                "work_id": "work_research",
                "left_document_version_id": "version_left",
                "right_document_version_id": "version_right",
            },
        ),
        ("trace_citations", {"project_id": system.project_id, "work_id": "work_research"}),
        ("trace_policy_links", {"project_id": system.project_id, "work_id": "work_research"}),
        (
            "export_project",
            {
                "project_id": system.project_id,
                "table_id": system.table_id,
                "format": "json",
                "expected_revision": system.project_revision,
                "idempotency_key": "api-export-step5",
            },
        ),
    )
    for name, body in calls:
        response = client.post(f"/v1/agent-tools/{name}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["tool_name"] == name
    invalid = client.post(
        "/v1/agent-tools/search_literature",
        json={"query": "inflation", "limit": 21},
    )
    assert invalid.status_code == 422
    assert invalid.json()["retryable"] is False


def test_sql_tool_audit_round_trip_and_domain_invariants() -> None:
    """SQL and memory providers preserve the same immutable audit contract."""
    repository = SqlRepository(create_engine("sqlite+pysqlite:///:memory:"))
    sql_metadata.create_all(repository._engine)  # type: ignore[attr-defined]
    item = ToolInvocationRecord(
        id="toolrun_sql",
        actor_id="economist",
        tool_name=AgentToolName.GET_WORK,
        arguments_sha256=_SHA_A,
        outcome=ToolInvocationOutcome.SUCCESS,
        result_sha256=_SHA_B,
        target_ids=("work_1",),
        duration_ms=4,
        occurred_at=_NOW,
    )
    repository.put_tool_invocation(item)
    repository.put_tool_invocation(item)
    assert repository.list_tool_invocations() == (item,)
    assert repository.list_tool_invocations(limit=0) == ()
    with pytest.raises(ValueError, match="digest"):
        ToolInvocationRecord(
            id="bad",
            actor_id="actor",
            tool_name=AgentToolName.GET_WORK,
            arguments_sha256="bad",
            outcome=ToolInvocationOutcome.ERROR,
            result_sha256=None,
            target_ids=(),
            duration_ms=0,
            occurred_at=_NOW,
        )
    valid = {
        "id": "toolrun_invariant",
        "actor_id": "actor",
        "tool_name": AgentToolName.GET_WORK,
        "arguments_sha256": _SHA_A,
        "outcome": ToolInvocationOutcome.ERROR,
        "result_sha256": None,
        "target_ids": (),
        "duration_ms": 0,
        "occurred_at": _NOW,
    }
    for changes, message in (
        ({"id": ""}, "identity"),
        ({"result_sha256": "bad"}, "result digest"),
        ({"duration_ms": -1}, "duration"),
        ({"occurred_at": datetime(2026, 8, 6)}, "timezone-aware UTC"),
        ({"target_ids": ("",)}, "target identifiers"),
    ):
        with pytest.raises(EconomicEvidenceGraphError, match=message):
            ToolInvocationRecord(**{**valid, **changes})  # type: ignore[arg-type]
    repository.close()


def test_python_client_pagination_tools_exports_jobs_and_problem_details() -> None:
    """The Python wrapper validates responses and preserves structured failures."""
    search_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_calls
        if request.url.path == "/v1/search":
            search_calls += 1
            cursor = None if search_calls == 2 else "next"
            return httpx.Response(200, json=_search_response(cursor=cursor, rank=search_calls))
        if request.url.path == "/v1/agent-tools/compare_evidence":
            return httpx.Response(
                200,
                json={
                    "schema_version": "eeg.agent-tools.v1",
                    "invocation_id": "toolrun_1",
                    "tool_name": "compare_evidence",
                    "result": {"rows": []},
                },
            )
        if request.url.path == "/v1/agent-tools/export_project":
            return httpx.Response(
                200,
                json={
                    "schema_version": "eeg.agent-tools.v1",
                    "invocation_id": "toolrun_2",
                    "tool_name": "export_project",
                    "result": _project_export_response(),
                },
            )
        if request.url.path == "/v1/evidence/evidence_1":
            return httpx.Response(200, json=_evidence_response())
        if request.url.path == "/v1/projects" and request.method == "POST":
            return httpx.Response(201, json=_project_response())
        if request.url.path == "/v1/projects" and request.method == "GET":
            return httpx.Response(200, json=[_project_response()])
        if request.url.path == "/v1/projects/project_1":
            return httpx.Response(200, json=_project_bundle_response())
        if request.url.path.endswith("/data"):
            return httpx.Response(200, content=b"[]", headers={"content-type": "application/json"})
        if request.url.path == "/v1/jobs/job_1":
            return httpx.Response(200, json=_job_response())
        if request.url.path == "/v1/jobs/job_pending":
            return httpx.Response(200, json=_job_response(state="pending"))
        if request.url.path == "/v1/works/invalid-json":
            return httpx.Response(200, text="not-json")
        if request.url.path == "/v1/works/invalid-problem":
            return httpx.Response(500, text="not-json")
        if request.url.path == "/v1/works/missing":
            return httpx.Response(
                404,
                json={
                    "type": "https://example.invalid/not-found",
                    "title": "Not Found",
                    "status": 404,
                    "detail": "work missing",
                    "instance": request.url.path,
                    "code": "resource_not_found",
                    "request_id": "request_1",
                    "retryable": False,
                    "context": {"work_id": "missing"},
                },
            )
        return httpx.Response(500, text="unexpected")

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = EegClient(actor_id="economist", http_client=raw)
    with client as entered:
        assert entered is client
    results = list(client.iter_search(SearchCreateRequest(query="inflation", limit=1)))
    assert [item.rank for item in results] == [1, 2]
    with pytest.raises(ValueError, match="max_pages"):
        list(client.iter_search(SearchCreateRequest(query="inflation"), max_pages=0))
    assert client.retrieve_evidence("evidence_1").evidence_unit_id == "evidence_1"
    created = client.create_project(ProjectCreateRequest(name="Test", idempotency_key="create-project"))
    assert created.id == "project_1"
    assert client.list_projects() == [created]
    assert client.get_project(created.id).project["id"] == created.id
    assert client.compare_evidence("project_1", "table_1").result == {"rows": []}
    exported = client.export_project(
        "project_1",
        "table_1",
        expected_revision=1,
        idempotency_key="client-export",
    )
    assert exported.id == "export_1"
    assert client.download_project_export(exported.id) == b"[]"
    assert client.wait_for_job("job_1", timeout=1).state == "succeeded"
    with pytest.raises(ValueError, match="positive"):
        client.wait_for_job("job_pending", timeout=0)
    with pytest.raises(TimeoutError, match="did not finish"):
        client.wait_for_job("job_pending", timeout=0.000_001, poll_interval=0.000_001)
    with pytest.raises(EegClientError) as error:
        client.get_work("missing")
    assert (error.value.status, error.value.code, error.value.request_id, error.value.retryable) == (
        404,
        "resource_not_found",
        "request_1",
        False,
    )
    with pytest.raises(ValueError, match="unknown"):
        client.call_tool("not_a_tool", {})
    with pytest.raises(ValueError, match="actor_id"):
        EegClient(actor_id="")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        client.get_work("invalid-json")
    with pytest.raises(httpx.HTTPStatusError):
        client.get_work("invalid-problem")
    with EegClient(base_url="http://testserver") as owned:
        assert owned is not client
    raw.close()


def test_local_step5_operator_commands_emit_readiness_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can inspect readiness, audit, promotion, and the exact OpenAPI contract."""
    system = _tool_system()
    settings = Settings.from_environment(
        {
            "EEG_DATABASE_URL": "postgresql+psycopg://eeg@127.0.0.1/eeg",
            "EEG_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            "EEG_SOURCE_REGISTRY": str(_ROOT / "config/source-registry.toml"),
            "EEG_MINIMUM_FREE_SPACE_GIB": "1",
        }
    )
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "create_local_container", lambda _settings: system.container)

    assert cli.main(["readiness"]) == 0
    readiness = json.loads(capsys.readouterr().out)
    assert readiness["ready"] is True
    promotion_path = tmp_path / "promotion.json"
    assert cli.main(["promotion-manifest", "--output", str(promotion_path)]) == 0
    capsys.readouterr()
    assert json.loads(promotion_path.read_text(encoding="utf-8"))["manifest_sha256"]
    openapi_path = tmp_path / "openapi.json"
    assert cli.main(["export-openapi", "--output", str(openapi_path)]) == 0
    capsys.readouterr()
    assert "/v1/agent-tools/search_literature" in json.loads(openapi_path.read_text(encoding="utf-8"))["paths"]
    assert cli.main(["tool-audit", "--limit", "10"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_r_client_package_has_the_same_eight_tools_and_open_source_dependencies() -> None:
    """The checked-in R package exposes the shared workflow without paid dependencies."""
    package = _ROOT / "clients/r/eegR"
    namespace = (package / "NAMESPACE").read_text(encoding="utf-8")
    source = (package / "R/client.R").read_text(encoding="utf-8")
    description = (package / "DESCRIPTION").read_text(encoding="utf-8")
    assert "export(eeg_search_all)" in namespace
    assert "export(eeg_compare_evidence)" in namespace
    assert "export(eeg_export_project)" in namespace
    assert all(f'"{item.value}"' in source for item in AgentToolName)
    assert "httr2" in description
    assert all(name not in description.casefold() for name in ("aws", "openai", "anthropic"))


def test_readiness_and_promotion_manifest_are_machine_readable_and_checksum_addressed(tmp_path: Path) -> None:
    """Local operations expose provider health and seal portable compatibility metadata."""
    system = _tool_system()
    report = LocalReadinessService(
        catalog=system.repository,
        artifacts=system.artifacts,
        search_repository=system.repository,
        search_index=system.index,
        artifact_root=tmp_path,
        minimum_free_space_gib=1,
        database_revision=system.repository.current_migration_revision,
    ).report()
    assert report.ready
    assert all(item.status.value == "pass" for item in report.checks)
    manifest = PromotionManifestService(
        search_repository=system.repository,
        source_registry_path=_ROOT / "config/source-registry.toml",
        clock=FrozenClock(_NOW),
    ).create()
    digest = manifest.pop("manifest_sha256")
    assert digest == record_sha256(manifest)
    assert manifest["target_profile"] == "unassigned"

    class UnavailableSearch(InMemorySearchIndex):
        def check(self) -> None:
            raise ConnectionError("unavailable")

    unavailable_report = LocalReadinessService(
        catalog=system.repository,
        artifacts=system.artifacts,
        search_repository=system.repository,
        search_index=UnavailableSearch(),
        artifact_root=tmp_path,
        minimum_free_space_gib=1,
        database_revision=system.repository.current_migration_revision,
    ).report()
    assert not unavailable_report.ready
    assert next(item for item in unavailable_report.checks if item.name == "opensearch").detail == "ConnectionError"
    empty = InMemoryRepository()
    with pytest.raises(ValueError, match="active index"):
        PromotionManifestService(
            search_repository=empty,
            source_registry_path=_ROOT / "config/source-registry.toml",
            clock=FrozenClock(_NOW),
        ).create()


def _search_response(*, cursor: str | None, rank: int) -> dict[str, object]:
    """Return one valid client search page."""
    return {
        "schema_version": "eeg.api.v1",
        "query": "inflation",
        "parsed_terms": ["inflation"],
        "results": [
            {
                "rank": rank,
                "score": 0.1,
                "work_id": f"work_{rank}",
                "document_version_id": f"version_{rank}",
                "source_record_id": f"record_{rank}",
                "title": "Inflation",
                "authors": ["Ada Economist"],
                "published_at": "2024-01-01",
                "source_code": "crossref",
                "source_identifier": "10.0000/test",
                "source_url": "https://api.crossref.org/works/test",
                "policy_version": 1,
                "alternative_version_count": 0,
                "version_selection_reason": "highest score",
                "contributions": [{"channel": "lexical_work", "rank": rank, "raw_score": 1.0, "contribution": 0.1}],
                "matched_phrases": ["inflation"],
                "evidence": None,
                "temporal": {
                    "cutoff": None,
                    "eligible": True,
                    "eligible_from": "2024-01-01T00:00:00Z",
                    "date_basis": "published",
                    "date_evidence": "2024-01-01",
                    "uncertainty": None,
                    "safety_class": "T0",
                },
                "warnings": [],
            }
        ],
        "index_generation_id": "idxgen_1",
        "corpus_snapshot_id": "snapshot_1",
        "retrieval_config_id": "retrieval_1",
        "modes_used": ["lexical_work"],
        "omitted_channels": [],
        "warnings": [],
        "next_cursor": cursor,
        "search_run_id": None,
    }


def _project_export_response() -> dict[str, object]:
    """Return one valid client project export response."""
    return {
        "id": "export_1",
        "project_id": "project_1",
        "table_id": "table_1",
        "format": "json",
        "project_revision": 1,
        "data_uri": "artifact://sha256/data",
        "data_sha256": _SHA_A,
        "data_media_type": "application/json",
        "data_byte_length": 2,
        "manifest_uri": "artifact://sha256/manifest",
        "manifest_sha256": _SHA_B,
        "manifest_byte_length": 10,
        "warnings": [],
        "created_by": "economist",
        "created_at": "2026-08-06T01:00:00Z",
        "idempotency_key": "client-export",
    }


def _job_response(*, state: str = "succeeded") -> dict[str, object]:
    """Return one valid completed job response."""
    return {
        "id": "job_1",
        "kind": "export",
        "state": state,
        "idempotency_key": "job-key",
        "input": {},
        "created_at": "2026-08-06T01:00:00Z",
        "started_at": None if state == "pending" else "2026-08-06T01:00:00Z",
        "finished_at": None if state == "pending" else "2026-08-06T01:00:01Z",
        "output": None if state == "pending" else {},
        "error_code": None,
        "error_message": None,
    }


def _evidence_response() -> dict[str, object]:
    """Return one valid policy-gated evidence response."""
    return {
        "evidence_unit_id": "evidence_1",
        "normalized_document_id": "document_1",
        "acquired_asset_id": "asset_1",
        "text": "A bounded excerpt.",
        "access_mode": "display_excerpt",
        "section_path": ["Findings"],
        "locator": "paragraph:1",
        "page_start": 1,
        "page_end": 1,
        "content_sha256": _SHA_A,
        "text_sha256": _SHA_B,
        "retrieved_at": "2026-08-06T01:00:00Z",
        "parser_name": "plain_text",
        "parser_version": "1.0",
        "extraction_schema_version": "eeg.normalized.v1",
        "warnings": [],
    }


def _project_response() -> dict[str, object]:
    """Return one valid project summary."""
    return {
        "id": "project_1",
        "name": "Test",
        "description": "",
        "created_by": "economist",
        "created_at": "2026-08-06T01:00:00Z",
        "baseline_corpus_snapshot_id": "snapshot_1",
        "baseline_index_generation_id": "idxgen_1",
        "idempotency_key": "create-project",
        "revision": 1,
    }


def _project_bundle_response() -> dict[str, object]:
    """Return one valid empty project handoff."""
    return {
        "schema_version": "eeg.api.v1",
        "project": _project_response(),
        "members": [],
        "search_runs": [],
        "screening_events": [],
        "notes": [],
        "evidence_records": [],
        "evidence_tables": [],
        "evidence_table_rows": [],
        "policy_links": [],
        "review_events": [],
        "exports": [],
    }
