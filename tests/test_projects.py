"""Step 4 projects, evidence review, policy links, exports, SQL, and API tests."""

from __future__ import annotations

import json
from asyncio import run
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine

from economic_evidence_graph.api import create_app
from economic_evidence_graph.application import (
    EvidenceAnchorInput,
    EvidenceFieldInput,
    ProjectService,
    RetrievalService,
    TableCellInput,
)
from economic_evidence_graph.bootstrap import Container
from economic_evidence_graph.domain import (
    EvidenceLineageMethod,
    EvidenceState,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    HistoricalMode,
    IndexGeneration,
    PolicyLink,
    PolicyLinkType,
    Project,
    ProjectBundle,
    ProjectEvidenceAnchor,
    ProjectEvidenceField,
    ProjectEvidenceRecord,
    ProjectExport,
    ProjectExportFormat,
    ProjectMember,
    ProjectNote,
    ProjectRole,
    ReviewEvent,
    ReviewTargetType,
    ScreeningEvent,
    ScreeningOutcome,
    SearchEvidenceDocument,
    SearchRun,
    SuppressionRecord,
    TemporalSafetyClass,
    review_state,
)
from economic_evidence_graph.exceptions import (
    AuthorizationError,
    ConflictError,
    EconomicEvidenceGraphError,
    NotFoundError,
)
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
from economic_evidence_graph.serialization import record_sha256

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)
_SHA_A = "a" * 64
_SHA_B = "b" * 64


@dataclass(frozen=True, slots=True)
class ProjectSystem:
    """Fully composed Step 4 test system."""

    repository: InMemoryRepository | SqlRepository
    artifacts: InMemoryArtifactStore
    index: InMemorySearchIndex
    retrieval: RetrievalService
    service: ProjectService
    generation: IndexGeneration
    research: SearchEvidenceDocument
    policy: SearchEvidenceDocument


class ApiClient:
    """Small synchronous ASGI client supporting actor headers."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str, *, actor: str = "owner") -> httpx.Response:
        """Issue an actor-attributed GET."""
        return run(self._request("GET", path, headers={"X-EEG-Actor": actor}))

    def post(self, path: str, *, json: object, actor: str = "owner") -> httpx.Response:
        """Issue an actor-attributed JSON POST."""
        return run(
            self._request(
                "POST",
                path,
                json=json,
                headers={"X-EEG-Actor": actor},
            )
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Call the public ASGI interface."""
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)


def _evidence(
    identifier: str,
    *,
    work_id: str,
    version_id: str,
    source_code: str,
    source_url: str,
    text: str,
) -> SearchEvidenceDocument:
    """Build one complete current evidence projection."""
    embeddings = DeterministicEconomicsEmbedding(64)
    return SearchEvidenceDocument(
        id=identifier,
        work_id=work_id,
        document_version_id=version_id,
        source_record_id=f"meta_{identifier}",
        normalized_document_id=f"doc_{identifier}",
        acquired_asset_id=f"asset_{identifier}",
        source_code=source_code,
        source_identifier=identifier,
        source_url=source_url,
        content_url=source_url,
        title=f"Title for {identifier}",
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
        locator="paragraph:1",
        page_start=1,
        page_end=1,
        parser_name="plain_text",
        parser_version="1.0",
        extraction_schema_version="eeg.normalized.v1",
        text_sha256=_SHA_A,
        content_sha256=_SHA_B,
        retrieved_at=_NOW,
        rights_status="rights_unknown",
        quality_warnings=(),
        version_count=1,
        embedding=embeddings.embed(text),
    )


def _system(repository: InMemoryRepository | SqlRepository | None = None) -> ProjectSystem:
    """Compose search, policy, project persistence, and artifact services."""
    repo = repository or InMemoryRepository()
    artifacts = InMemoryArtifactStore()
    index = InMemorySearchIndex()
    embeddings = DeterministicEconomicsEmbedding(64)
    generation = IndexGeneration(
        id="idxgen_step4",
        mapping_version="eeg-search-v1",
        retrieval_config_id="eeg-retrieval-v1",
        embedding_config_id=embeddings.config_id,
        corpus_snapshot_id="snapshot_step4",
        manifest_sha256=_SHA_A,
        work_index_name="work-step4",
        evidence_index_name="evidence-step4",
        work_document_count=0,
        evidence_document_count=2,
        created_at=_NOW,
        activated_at=_NOW,
    )
    research = _evidence(
        "evidence_research",
        work_id="work_research",
        version_id="version_research",
        source_code="crossref",
        source_url="https://api.crossref.org/works/research",
        text="The estimated output response is minus 1.5 percent after four quarters.",
    )
    policy = _evidence(
        "evidence_policy",
        work_id="work_policy",
        version_id="version_policy",
        source_code="board_fomc",
        source_url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20240101.htm",
        text="The Committee cited the research estimate when assessing policy transmission.",
    )
    index.replace_generation(generation, (), (research, policy), embedding_dimension=64)
    repo.put_index_generation(generation)
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    retrieval = RetrievalService(
        repository=repo,
        index=index,
        embeddings=embeddings,
        policy=PolicyEngine(registry),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("step4-search"),
    )
    service = ProjectService(
        repository=repo,
        search_repository=repo,
        search_index=index,
        retrieval=retrieval,
        artifacts=artifacts,
        clock=FrozenClock(_NOW),
    )
    return ProjectSystem(repo, artifacts, index, retrieval, service, generation, research, policy)


def _field(
    *,
    state: EvidenceState = EvidenceState.OBSERVED,
    lineage: EvidenceLineageMethod = EvidenceLineageMethod.OBSERVED_TRANSCRIPTION,
    anchor: bool = True,
) -> EvidenceFieldInput:
    """Build one estimate field proposal."""
    return EvidenceFieldInput(
        field_name="estimate",
        display_value="-1.5",
        normalized_value=-1.5,
        units="percent",
        applicability="four-quarter baseline response",
        missingness_reason=None,
        state=state,
        lineage_method=lineage,
        confidence=0.99,
        anchors=(
            EvidenceAnchorInput(
                "evidence_research",
                "estimated output response is minus 1.5 percent",
            ),
        )
        if anchor
        else (),
    )


def _create_project(system: ProjectSystem, *, actor: str = "owner", key: str = "create") -> Project:
    """Create one pinned project."""
    return system.service.create(
        name="Monetary policy estimates",
        description="RA screening and estimate comparison",
        actor_id=actor,
        idempotency_key=key,
    )


def test_complete_project_handoff_review_policy_trace_and_exports() -> None:
    """Economists can execute the complete Step 4 signature workflow with exact lineage."""
    system = _system()
    project = _create_project(system)
    assert project.baseline_corpus_snapshot_id == system.generation.corpus_snapshot_id
    assert _create_project(system) == project
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="ra",
        role=ProjectRole.EDITOR,
        expected_revision=project.revision,
        idempotency_key="add-ra",
    )
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="reviewer",
        role=ProjectRole.REVIEWER,
        expected_revision=project.revision,
        idempotency_key="add-reviewer",
    )
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="viewer",
        role=ProjectRole.VIEWER,
        expected_revision=project.revision,
        idempotency_key="add-viewer",
    )
    run_item = SearchRun(
        id="run_step4",
        request_payload={"query": "policy transmission"},
        response_payload={"results": ["work_research"]},
        index_generation_id=system.generation.id,
        query_sha256=record_sha256("policy transmission"),
        created_at=_NOW,
    )
    system.repository.put_search_run(run_item)
    project = system.service.attach_search_run(
        project.id,
        actor_id="ra",
        search_run_id=run_item.id,
        expected_revision=project.revision,
        idempotency_key="save-run",
    )
    assert (
        system.service.attach_search_run(
            project.id,
            actor_id="ra",
            search_run_id=run_item.id,
            expected_revision=1,
            idempotency_key="same-run-new-operation",
        )
        == project
    )
    project = system.service.screen(
        project.id,
        actor_id="ra",
        work_id="work_research",
        document_version_id="version_research",
        outcome=ScreeningOutcome.INCLUDE,
        reason_code=None,
        note="Directly estimates the target response.",
        expected_revision=project.revision,
        idempotency_key="screen-include",
    )
    project = system.service.screen(
        project.id,
        actor_id="ra",
        work_id="work_other",
        document_version_id=None,
        outcome=ScreeningOutcome.EXCLUDE,
        reason_code="out_of_scope",
        note=None,
        expected_revision=project.revision,
        idempotency_key="screen-exclude",
    )
    project = system.service.add_note(
        project.id,
        actor_id="ra",
        target_type="work",
        target_id="work_research",
        body="Verify the reported horizon before policy briefing.",
        expected_revision=project.revision,
        idempotency_key="note-1",
    )
    project = system.service.add_evidence_record(
        project.id,
        actor_id="ra",
        work_id="work_research",
        document_version_id="version_research",
        record_type="estimate",
        fields=(_field(),),
        expected_revision=project.revision,
        idempotency_key="evidence-1",
    )
    bundle = system.service.get(project.id, actor_id="reviewer")
    record = bundle.evidence_records[0]
    field = record.fields[0]
    assert field.anchors[0].content_sha256 == _SHA_B
    project = system.service.review(
        project.id,
        actor_id="reviewer",
        target_type=ReviewTargetType.EVIDENCE_FIELD,
        target_id=field.id,
        new_state=EvidenceState.APPROVED,
        reason="Quote, units, horizon, and value verified.",
        expected_revision=project.revision,
        idempotency_key="review-field",
    )
    project = system.service.create_evidence_table(
        project.id,
        actor_id="ra",
        name="Output response estimates",
        description="Comparable four-quarter estimates",
        columns=(EvidenceTableColumn("estimate", "Estimate", "number", "percent"),),
        expected_revision=project.revision,
        idempotency_key="table-1",
    )
    table = system.service.get(project.id, actor_id="ra").evidence_tables[0]
    project = system.service.add_evidence_table_row(
        project.id,
        table.id,
        actor_id="ra",
        evidence_record_id=record.id,
        label="Ada Economist (2024), baseline",
        cells=(TableCellInput("estimate", field.id),),
        expected_revision=project.revision,
        idempotency_key="row-1",
    )
    project = system.service.add_policy_link(
        project.id,
        actor_id="ra",
        research_work_id="work_research",
        policy_work_id="work_policy",
        relationship_type=PolicyLinkType.EXPLICIT_MENTION,
        anchor=EvidenceAnchorInput("evidence_policy", "Committee cited the research estimate"),
        method="manual_review",
        confidence=1,
        state=EvidenceState.OBSERVED,
        expected_revision=project.revision,
        idempotency_key="policy-link",
    )
    link = system.service.trace_policy_links(project.id, actor_id="owner", work_id="work_research")[0]
    assert link.relationship_type is PolicyLinkType.EXPLICIT_MENTION
    project = system.service.review(
        project.id,
        actor_id="reviewer",
        target_type=ReviewTargetType.POLICY_LINK,
        target_id=link.id,
        new_state=EvidenceState.APPROVED,
        reason="Policy passage explicitly names the research estimate.",
        expected_revision=project.revision,
        idempotency_key="review-link",
    )
    exports = []
    for ordinal, format_value in enumerate(ProjectExportFormat):
        exported = system.service.export_table(
            project.id,
            table.id,
            actor_id="owner",
            format=format_value,
            expected_revision=project.revision,
            idempotency_key=f"export-{format_value.value}",
        )
        exports.append(exported)
        project = system.repository.get_project(project.id)
        ref, same = system.service.export_artifact(exported.id, actor_id="reviewer", manifest=False)
        assert same == exported
        assert system.artifacts.get_bytes(ref)
        if ordinal == 0:
            assert (
                system.service.export_table(
                    project.id,
                    table.id,
                    actor_id="owner",
                    format=format_value,
                    expected_revision=1,
                    idempotency_key=f"export-{format_value.value}",
                )
                == exported
            )
            manifest_ref, _ = system.service.export_artifact(exported.id, actor_id="owner", manifest=True)
            manifest = json.loads(system.artifacts.get_bytes(manifest_ref))
            assert manifest["export_id"] == exported.id
            assert manifest["evidence_dependencies"][0]["evidence_unit_id"] == "evidence_research"
    assert exports[0].data_media_type == "application/json"
    assert exports[1].data_media_type == "text/csv"
    assert exports[2].data_media_type == "text/markdown"
    csv_ref, _ = system.service.export_artifact(exports[1].id, actor_id="owner", manifest=False)
    csv_data = system.artifacts.get_bytes(csv_ref)
    assert b"estimate__review_state" in csv_data
    assert b"evidence_research" in csv_data
    assert b"'-1.5" in csv_data
    final = system.service.get(project.id, actor_id="owner")
    assert len(final.members) == 4
    assert len(final.search_runs) == 1
    assert len(final.screening_events) == 2
    assert len(final.review_events) == 2
    assert len(final.exports) == 3


def test_authorization_concurrency_idempotency_and_invalid_workflows_fail_closed() -> None:
    """Project authorization, anchor integrity, review state, and revisions fail closed."""
    system = _system()
    project = _create_project(system)
    with pytest.raises(AuthorizationError, match="not a member"):
        system.service.get(project.id, actor_id="outsider")
    with pytest.raises(NotFoundError, match="saved search"):
        system.service.attach_search_run(
            project.id,
            actor_id="owner",
            search_run_id="missing",
            expected_revision=project.revision,
            idempotency_key="missing-run",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="reason code"):
        system.service.screen(
            project.id,
            actor_id="owner",
            work_id="work",
            document_version_id=None,
            outcome=ScreeningOutcome.EXCLUDE,
            reason_code=None,
            note=None,
            expected_revision=project.revision,
            idempotency_key="bad-screen",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="different work"):
        system.service.add_evidence_record(
            project.id,
            actor_id="owner",
            work_id="wrong",
            document_version_id="version_research",
            record_type="estimate",
            fields=(_field(),),
            expected_revision=project.revision,
            idempotency_key="wrong-work",
        )
    bad_quote = replace(
        _field(),
        anchors=(EvidenceAnchorInput("evidence_research", "quote that is absent"),),
    )
    with pytest.raises(EconomicEvidenceGraphError, match="not present"):
        system.service.add_evidence_record(
            project.id,
            actor_id="owner",
            work_id="work_research",
            document_version_id="version_research",
            record_type="estimate",
            fields=(bad_quote,),
            expected_revision=project.revision,
            idempotency_key="bad-quote",
        )
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="viewer",
        role=ProjectRole.VIEWER,
        expected_revision=project.revision,
        idempotency_key="viewer",
    )
    with pytest.raises(AuthorizationError, match="requires"):
        system.service.add_note(
            project.id,
            actor_id="viewer",
            target_type="work",
            target_id="work_research",
            body="not allowed",
            expected_revision=project.revision,
            idempotency_key="viewer-note",
        )
    with pytest.raises(ConflictError, match="concurrently"):
        system.service.add_note(
            project.id,
            actor_id="owner",
            target_type="work",
            target_id="work_research",
            body="stale",
            expected_revision=1,
            idempotency_key="stale-note",
        )
    project = system.service.add_evidence_record(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        record_type="estimate",
        fields=(_field(),),
        expected_revision=project.revision,
        idempotency_key="valid-record",
    )
    field = system.service.get(project.id, actor_id="owner").evidence_records[0].fields[0]
    with pytest.raises(AuthorizationError, match="reviewer"):
        system.service.review(
            project.id,
            actor_id="viewer",
            target_type=ReviewTargetType.EVIDENCE_FIELD,
            target_id=field.id,
            new_state=EvidenceState.APPROVED,
            reason="not authorized",
            expected_revision=project.revision,
            idempotency_key="bad-reviewer",
        )
    project = system.service.review(
        project.id,
        actor_id="owner",
        target_type=ReviewTargetType.EVIDENCE_FIELD,
        target_id=field.id,
        new_state=EvidenceState.REJECTED,
        reason="Units not supported.",
        expected_revision=project.revision,
        idempotency_key="reject",
    )
    with pytest.raises(EconomicEvidenceGraphError, match="invalid"):
        system.service.review(
            project.id,
            actor_id="owner",
            target_type=ReviewTargetType.EVIDENCE_FIELD,
            target_id=field.id,
            new_state=EvidenceState.APPROVED,
            reason="try to reverse terminal state",
            expected_revision=project.revision,
            idempotency_key="reverse",
        )


def test_project_service_defensive_branches_are_explicit() -> None:
    """Membership, table, anchor, review, and export errors remain specific and recoverable."""
    system = _system()
    project = _create_project(system)
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="reviewer",
        role=ProjectRole.REVIEWER,
        expected_revision=project.revision,
        idempotency_key="def-reviewer",
    )
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="viewer",
        role=ProjectRole.VIEWER,
        expected_revision=project.revision,
        idempotency_key="def-viewer",
    )
    with pytest.raises(AuthorizationError, match="only a project owner"):
        system.service.add_member(
            project.id,
            actor_id="reviewer",
            member_actor_id="new",
            role=ProjectRole.EDITOR,
            expected_revision=project.revision,
            idempotency_key="not-owner",
        )
    assert (
        system.service.add_member(
            project.id,
            actor_id="owner",
            member_actor_id="viewer",
            role=ProjectRole.VIEWER,
            expected_revision=1,
            idempotency_key="ignored-retry-key",
        )
        == project
    )
    with pytest.raises(ConflictError, match="roles are immutable"):
        system.service.add_member(
            project.id,
            actor_id="owner",
            member_actor_id="viewer",
            role=ProjectRole.EDITOR,
            expected_revision=project.revision,
            idempotency_key="change-role",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="actor identifier"):
        system.service.list(actor_id="")
    for evidence_input, message in (
        (replace(_field(), state=EvidenceState.APPROVED), "must begin"),
        (
            _field(
                state=EvidenceState.OBSERVED,
                lineage=EvidenceLineageMethod.USER_AUTHORED,
                anchor=False,
            ),
            "cannot be labeled",
        ),
    ):
        with pytest.raises(EconomicEvidenceGraphError, match=message):
            system.service.add_evidence_record(
                project.id,
                actor_id="owner",
                work_id="work_research",
                document_version_id="version_research",
                record_type="estimate",
                fields=(evidence_input,),
                expected_revision=project.revision,
                idempotency_key=f"initial-{message}",
            )
    for anchor, version, message in (
        (EvidenceAnchorInput("missing", "quote"), "version_research", "not found"),
        (
            EvidenceAnchorInput("evidence_research", "estimated output response"),
            "wrong-version",
            "different document version",
        ),
    ):
        with pytest.raises((NotFoundError, EconomicEvidenceGraphError), match=message):
            system.service.add_evidence_record(
                project.id,
                actor_id="owner",
                work_id="work_research",
                document_version_id=version,
                record_type="estimate",
                fields=(replace(_field(), anchors=(anchor,)),),
                expected_revision=project.revision,
                idempotency_key=f"anchor-{version}",
            )
    project = system.service.add_evidence_record(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        record_type="estimate",
        fields=(_field(),),
        expected_revision=project.revision,
        idempotency_key="def-record",
    )
    bundle = system.service.get(project.id, actor_id="owner")
    record, field = bundle.evidence_records[0], bundle.evidence_records[0].fields[0]
    project = system.service.create_evidence_table(
        project.id,
        actor_id="owner",
        name="Defensive table",
        description="",
        columns=(EvidenceTableColumn("estimate", "Estimate", "number"),),
        expected_revision=project.revision,
        idempotency_key="def-table",
    )
    table = system.service.get(project.id, actor_id="owner").evidence_tables[0]
    with pytest.raises(EconomicEvidenceGraphError, match="every declared column"):
        system.service.add_evidence_table_row(
            project.id,
            table.id,
            actor_id="owner",
            evidence_record_id=record.id,
            label="missing columns",
            cells=(TableCellInput("other", missingness_reason="not_reported"),),
            expected_revision=project.revision,
            idempotency_key="bad-columns",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="outside its record"):
        system.service.add_evidence_table_row(
            project.id,
            table.id,
            actor_id="owner",
            evidence_record_id=record.id,
            label="bad field",
            cells=(TableCellInput("estimate", "missing-field"),),
            expected_revision=project.revision,
            idempotency_key="bad-field",
        )
    project = system.service.create_evidence_table(
        project.id,
        actor_id="owner",
        name="Mismatched field",
        description="",
        columns=(EvidenceTableColumn("other", "Other", "number"),),
        expected_revision=project.revision,
        idempotency_key="other-table",
    )
    other_table = next(
        item for item in system.service.get(project.id, actor_id="owner").evidence_tables if item.id != table.id
    )
    with pytest.raises(EconomicEvidenceGraphError, match="does not match"):
        system.service.add_evidence_table_row(
            project.id,
            other_table.id,
            actor_id="owner",
            evidence_record_id=record.id,
            label="wrong field",
            cells=(TableCellInput("other", field.id),),
            expected_revision=project.revision,
            idempotency_key="wrong-field",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="must begin"):
        system.service.add_policy_link(
            project.id,
            actor_id="owner",
            research_work_id="work_research",
            policy_work_id="work_policy",
            relationship_type=PolicyLinkType.TOPICAL_SUGGESTION,
            anchor=None,
            method="manual_review",
            confidence=1,
            state=EvidenceState.APPROVED,
            expected_revision=project.revision,
            idempotency_key="bad-link-state",
        )
    with pytest.raises(NotFoundError, match="review target"):
        system.service.review(
            project.id,
            actor_id="reviewer",
            target_type=ReviewTargetType.EVIDENCE_FIELD,
            target_id="missing",
            new_state=EvidenceState.APPROVED,
            reason="missing",
            expected_revision=project.revision,
            idempotency_key="missing-review-target",
        )
    project = system.service.review(
        project.id,
        actor_id="reviewer",
        target_type=ReviewTargetType.EVIDENCE_FIELD,
        target_id=field.id,
        new_state=EvidenceState.APPROVED,
        reason="verified",
        expected_revision=project.revision,
        idempotency_key="def-review",
    )
    assert (
        system.service.review(
            project.id,
            actor_id="reviewer",
            target_type=ReviewTargetType.EVIDENCE_FIELD,
            target_id=field.id,
            new_state=EvidenceState.APPROVED,
            reason="verified",
            expected_revision=1,
            idempotency_key="def-review",
        )
        == project
    )
    with pytest.raises(ConflictError, match="reused"):
        system.service.review(
            project.id,
            actor_id="reviewer",
            target_type=ReviewTargetType.EVIDENCE_FIELD,
            target_id=field.id,
            new_state=EvidenceState.APPROVED,
            reason="different reason",
            expected_revision=project.revision,
            idempotency_key="def-review",
        )
    with pytest.raises(AuthorizationError, match="viewers"):
        system.service.export_table(
            project.id,
            table.id,
            actor_id="viewer",
            format=ProjectExportFormat.JSON,
            expected_revision=project.revision,
            idempotency_key="viewer-export",
        )
    with pytest.raises(NotFoundError, match="evidence table"):
        system.service.export_table(
            project.id,
            "missing-table",
            actor_id="owner",
            format=ProjectExportFormat.JSON,
            expected_revision=project.revision,
            idempotency_key="missing-table-export",
        )
    project = system.service.add_evidence_table_row(
        project.id,
        table.id,
        actor_id="owner",
        evidence_record_id=record.id,
        label="valid row",
        cells=(TableCellInput("estimate", field.id),),
        expected_revision=project.revision,
        idempotency_key="def-valid-row",
    )
    with pytest.raises(ConflictError, match="already has a row"):
        system.service.add_evidence_table_row(
            project.id,
            table.id,
            actor_id="owner",
            evidence_record_id=record.id,
            label="duplicate row",
            cells=(TableCellInput("estimate", field.id),),
            expected_revision=project.revision,
            idempotency_key="def-duplicate-row",
        )
    system.service.export_table(
        project.id,
        table.id,
        actor_id="owner",
        format=ProjectExportFormat.JSON,
        expected_revision=project.revision,
        idempotency_key="def-export-key",
    )
    with pytest.raises(ConflictError, match="different export input"):
        system.service.export_table(
            project.id,
            table.id,
            actor_id="owner",
            format=ProjectExportFormat.CSV,
            expected_revision=1,
            idempotency_key="def-export-key",
        )


def test_explicit_missingness_user_authorship_and_suppression_safe_export() -> None:
    """Blank cells are explicit and later suppression removes stored quotes from new exports."""
    system = _system()
    project = _create_project(system)
    user_field = replace(
        _field(state=EvidenceState.SUGGESTED, lineage=EvidenceLineageMethod.USER_AUTHORED, anchor=False),
        display_value="Economist judgment",
        normalized_value=None,
        units=None,
    )
    project = system.service.add_evidence_record(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        record_type="assessment",
        fields=(user_field,),
        expected_revision=project.revision,
        idempotency_key="authored",
    )
    project = system.service.create_evidence_table(
        project.id,
        actor_id="owner",
        name="Missingness",
        description="Explicit unavailable values",
        columns=(EvidenceTableColumn("estimate", "Estimate", "text"),),
        expected_revision=project.revision,
        idempotency_key="missing-table",
    )
    bundle = system.service.get(project.id, actor_id="owner")
    table = bundle.evidence_tables[0]
    record = bundle.evidence_records[0]
    project = system.service.add_evidence_table_row(
        project.id,
        table.id,
        actor_id="owner",
        evidence_record_id=record.id,
        label="Unavailable estimate",
        cells=(TableCellInput("estimate", missingness_reason="not_reported"),),
        expected_revision=project.revision,
        idempotency_key="missing-row",
    )
    exported = system.service.export_table(
        project.id,
        table.id,
        actor_id="owner",
        format=ProjectExportFormat.CSV,
        expected_revision=project.revision,
        idempotency_key="missing-export",
    )
    ref, _ = system.service.export_artifact(exported.id, actor_id="owner", manifest=False)
    assert b"[not_reported]" in system.artifacts.get_bytes(ref)

    anchored_project = _create_project(system, key="anchored-project")
    anchored_project = system.service.add_evidence_record(
        anchored_project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        record_type="estimate",
        fields=(_field(),),
        expected_revision=anchored_project.revision,
        idempotency_key="anchored-record",
    )
    anchored_project = system.service.create_evidence_table(
        anchored_project.id,
        actor_id="owner",
        name="Suppressed",
        description="Suppression gate",
        columns=(EvidenceTableColumn("estimate", "Estimate", "number"),),
        expected_revision=anchored_project.revision,
        idempotency_key="anchored-table",
    )
    anchored_bundle = system.service.get(anchored_project.id, actor_id="owner")
    anchored_record = anchored_bundle.evidence_records[0]
    anchored_table = anchored_bundle.evidence_tables[0]
    anchored_project = system.service.add_evidence_table_row(
        anchored_project.id,
        anchored_table.id,
        actor_id="owner",
        evidence_record_id=anchored_record.id,
        label="Suppressed evidence",
        cells=(TableCellInput("estimate", anchored_record.fields[0].id),),
        expected_revision=anchored_project.revision,
        idempotency_key="anchored-row",
    )
    cast(InMemoryRepository, system.repository).put_suppression(
        SuppressionRecord(
            id="sup_step4",
            scope_type="asset",
            scope_value="asset_evidence_research",
            reason="rights request",
            created_at=_NOW,
            physical_deletion_requested=False,
        )
    )
    suppressed = system.service.export_table(
        anchored_project.id,
        anchored_table.id,
        actor_id="owner",
        format=ProjectExportFormat.JSON,
        expected_revision=anchored_project.revision,
        idempotency_key="suppressed-export",
    )
    assert suppressed.warnings
    assert "unavailable" in suppressed.warnings[0]
    suppressed_ref, _ = system.service.export_artifact(suppressed.id, actor_id="owner", manifest=False)
    data = json.loads(system.artifacts.get_bytes(suppressed_ref))
    assert data[0]["values"]["estimate"]["display_value"] is None
    assert data[0]["values"]["estimate"]["anchors"][0]["quote"] is None


def test_sql_repository_round_trips_every_project_record_type() -> None:
    """The SQL adapter persists and rehydrates the complete project handoff."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    sql_metadata.create_all(engine)
    repository = SqlRepository(engine)
    system = _system(repository)
    project = _create_project(system)
    project = system.service.add_member(
        project.id,
        actor_id="owner",
        member_actor_id="reviewer",
        role=ProjectRole.REVIEWER,
        expected_revision=project.revision,
        idempotency_key="sql-reviewer",
    )
    run_item = SearchRun(
        id="run_sql_step4",
        request_payload={"query": "sql"},
        response_payload={"results": []},
        index_generation_id=system.generation.id,
        query_sha256=record_sha256("sql"),
        created_at=_NOW,
    )
    repository.put_search_run(run_item)
    project = system.service.attach_search_run(
        project.id,
        actor_id="owner",
        search_run_id=run_item.id,
        expected_revision=project.revision,
        idempotency_key="sql-run",
    )
    project = system.service.screen(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        outcome=ScreeningOutcome.MAYBE,
        reason_code="needs_review",
        note="Check appendix.",
        expected_revision=project.revision,
        idempotency_key="sql-screen",
    )
    project = system.service.add_note(
        project.id,
        actor_id="owner",
        target_type="work",
        target_id="work_research",
        body="SQL note",
        expected_revision=project.revision,
        idempotency_key="sql-note",
    )
    project = system.service.add_evidence_record(
        project.id,
        actor_id="owner",
        work_id="work_research",
        document_version_id="version_research",
        record_type="estimate",
        fields=(_field(),),
        expected_revision=project.revision,
        idempotency_key="sql-evidence",
    )
    bundle = system.service.get(project.id, actor_id="owner")
    field = bundle.evidence_records[0].fields[0]
    project = system.service.review(
        project.id,
        actor_id="reviewer",
        target_type=ReviewTargetType.EVIDENCE_FIELD,
        target_id=field.id,
        new_state=EvidenceState.UNCERTAIN,
        reason="SQL review",
        expected_revision=project.revision,
        idempotency_key="sql-review",
    )
    project = system.service.create_evidence_table(
        project.id,
        actor_id="owner",
        name="SQL table",
        description="round trip",
        columns=(EvidenceTableColumn("estimate", "Estimate", "number", "percent"),),
        expected_revision=project.revision,
        idempotency_key="sql-table",
    )
    table = system.service.get(project.id, actor_id="owner").evidence_tables[0]
    project = system.service.add_evidence_table_row(
        project.id,
        table.id,
        actor_id="owner",
        evidence_record_id=bundle.evidence_records[0].id,
        label="SQL row",
        cells=(TableCellInput("estimate", field.id),),
        expected_revision=project.revision,
        idempotency_key="sql-row",
    )
    project = system.service.add_policy_link(
        project.id,
        actor_id="owner",
        research_work_id="work_research",
        policy_work_id="work_policy",
        relationship_type=PolicyLinkType.TOPICAL_SUGGESTION,
        anchor=None,
        method="deterministic_similarity",
        confidence=0.7,
        state=EvidenceState.SUGGESTED,
        expected_revision=project.revision,
        idempotency_key="sql-link",
    )
    exported = system.service.export_table(
        project.id,
        table.id,
        actor_id="owner",
        format=ProjectExportFormat.MARKDOWN,
        expected_revision=project.revision,
        idempotency_key="sql-export",
    )
    hydrated = repository.get_project_bundle(project.id)
    assert hydrated.project.revision == project.revision + 1
    assert {item.role for item in hydrated.members} == {ProjectRole.OWNER, ProjectRole.REVIEWER}
    assert hydrated.search_runs[0].search_run_id == run_item.id
    assert hydrated.screening_events[0].outcome is ScreeningOutcome.MAYBE
    assert hydrated.notes[0].body == "SQL note"
    assert hydrated.evidence_records[0].fields[0].anchors[0].locator == "paragraph:1"
    assert hydrated.evidence_tables[0].columns[0].units == "percent"
    assert hydrated.evidence_table_rows[0].cells[0].evidence_field_id == field.id
    assert hydrated.policy_links[0].relationship_type is PolicyLinkType.TOPICAL_SUGGESTION
    assert hydrated.review_events[0].new_state is EvidenceState.UNCERTAIN
    assert repository.get_project_export(exported.id) == exported
    assert repository.list_projects("owner")[0].id == project.id
    repository.close()


@pytest.mark.parametrize("backend", ["memory", "sql"])
def test_project_repository_conflicts_and_missing_state(backend: str) -> None:
    """Both persistence adapters enforce identical idempotency and revision behavior."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    if backend == "sql":
        sql_metadata.create_all(engine)
        repository: InMemoryRepository | SqlRepository = SqlRepository(engine)
    else:
        repository = InMemoryRepository()
    system = _system(repository)
    project = _create_project(system)
    with pytest.raises(NotFoundError, match="project was not found"):
        repository.get_project("missing")
    with pytest.raises(NotFoundError, match="project was not found"):
        repository.get_project_bundle("missing")
    with pytest.raises(NotFoundError, match="project export"):
        repository.get_project_export("missing")
    owner = repository.get_project_bundle(project.id).members[0]
    with pytest.raises(ConflictError, match="owner attribution"):
        repository.create_project(project, replace(owner, actor_id="other"))
    with pytest.raises(ConflictError, match="idempotency key"):
        repository.create_project(replace(project, name="different"), owner)
    note = ProjectNote("note_direct", project.id, "work", "work", "body", "owner", _NOW, "direct")
    revised = repository.put_project_note(note, expected_revision=project.revision)
    assert repository.put_project_note(note, expected_revision=1) == revised
    with pytest.raises(ConflictError, match="idempotency key"):
        repository.put_project_note(replace(note, body="different"), expected_revision=revised.revision)
    with pytest.raises(ConflictError, match="concurrently"):
        repository.put_project_note(
            ProjectNote("note_stale", project.id, "work", "work", "stale", "owner", _NOW, "stale"),
            expected_revision=1,
        )
    missing_note = ProjectNote("note_missing", "missing", "work", "work", "body", "owner", _NOW, "missing")
    with pytest.raises(NotFoundError, match="project was not found"):
        repository.put_project_note(missing_note, expected_revision=1)
    if backend == "sql":
        duplicate_actor = ProjectMember(
            "member_duplicate",
            project.id,
            "owner",
            ProjectRole.OWNER,
            "owner",
            _NOW,
            "duplicate",
        )
        with pytest.raises(ConflictError, match="uniqueness"):
            repository.put_project_member(duplicate_actor, expected_revision=revised.revision)
        repository.close()
    engine.dispose()


def test_project_api_supports_handoff_evidence_review_and_export(memory_system: object) -> None:
    """The Step 4 HTTP surface and workbench execute a complete bounded workflow."""
    system = _system()
    container: Container = replace(
        memory_system.container,  # type: ignore[attr-defined]
        artifacts=system.artifacts,
        retrieval=system.retrieval,
        search_index=system.index,
        projects=system.service,
    )
    client = ApiClient(create_app(container))
    assert "Research projects" in client.get("/projects").text
    created = client.post(
        "/v1/projects",
        json={"name": "API project", "description": "Step 4", "idempotency_key": "api-create"},
    )
    assert created.status_code == 201
    project = created.json()
    project_id = project["id"]
    assert client.get("/v1/projects").json()[0]["id"] == project_id
    project = client.post(
        f"/v1/projects/{project_id}/members",
        json={
            "actor_id": "reviewer",
            "role": "reviewer",
            "expected_revision": project["revision"],
            "idempotency_key": "api-reviewer",
        },
    ).json()
    project = client.post(
        f"/v1/projects/{project_id}/screening",
        json={
            "work_id": "work_research",
            "document_version_id": "version_research",
            "outcome": "include",
            "reason_code": None,
            "note": "API screening",
            "expected_revision": project["revision"],
            "idempotency_key": "api-screen",
        },
    ).json()
    api_run = SearchRun(
        "run_api_step4",
        {"query": "api"},
        {"results": []},
        system.generation.id,
        record_sha256("api"),
        _NOW,
    )
    system.repository.put_search_run(api_run)
    project = client.post(
        f"/v1/projects/{project_id}/search-runs",
        json={
            "search_run_id": api_run.id,
            "expected_revision": project["revision"],
            "idempotency_key": "api-run",
        },
    ).json()
    project = client.post(
        f"/v1/projects/{project_id}/notes",
        json={
            "target_type": "work",
            "target_id": "work_research",
            "body": "API note",
            "expected_revision": project["revision"],
            "idempotency_key": "api-note",
        },
    ).json()
    project = client.post(
        f"/v1/projects/{project_id}/evidence-records",
        json={
            "work_id": "work_research",
            "document_version_id": "version_research",
            "record_type": "estimate",
            "fields": [
                {
                    "field_name": "estimate",
                    "display_value": "-1.5",
                    "normalized_value": -1.5,
                    "units": "percent",
                    "applicability": "baseline",
                    "missingness_reason": None,
                    "state": "observed",
                    "lineage_method": "observed_transcription",
                    "confidence": 1,
                    "anchors": [
                        {
                            "evidence_unit_id": "evidence_research",
                            "quote": "estimated output response is minus 1.5 percent",
                        }
                    ],
                }
            ],
            "expected_revision": project["revision"],
            "idempotency_key": "api-evidence",
        },
    ).json()
    bundle = client.get(f"/v1/projects/{project_id}").json()
    field_id = bundle["evidence_records"][0]["fields"][0]["id"]
    project = client.post(
        f"/v1/projects/{project_id}/reviews",
        actor="reviewer",
        json={
            "target_type": "evidence_field",
            "target_id": field_id,
            "new_state": "approved",
            "reason": "API verified",
            "expected_revision": project["revision"],
            "idempotency_key": "api-review",
        },
    ).json()
    project = client.post(
        f"/v1/projects/{project_id}/policy-links",
        json={
            "research_work_id": "work_research",
            "policy_work_id": "work_policy",
            "relationship_type": "topical_suggestion",
            "anchor": None,
            "method": "manual_review",
            "confidence": 0.5,
            "state": "suggested",
            "expected_revision": project["revision"],
            "idempotency_key": "api-policy-link",
        },
    ).json()
    assert (
        client.get(f"/v1/projects/{project_id}/policy-links/work_research").json()[0]["relationship_type"]
        == "topical_suggestion"
    )
    project = client.post(
        f"/v1/projects/{project_id}/evidence-tables",
        json={
            "name": "API table",
            "description": "comparison",
            "columns": [{"key": "estimate", "label": "Estimate", "value_type": "number", "units": "percent"}],
            "expected_revision": project["revision"],
            "idempotency_key": "api-table",
        },
    ).json()
    bundle = client.get(f"/v1/projects/{project_id}").json()
    table_id = bundle["evidence_tables"][0]["id"]
    record_id = bundle["evidence_records"][0]["id"]
    project = client.post(
        f"/v1/projects/{project_id}/evidence-tables/{table_id}/rows",
        json={
            "evidence_record_id": record_id,
            "label": "API row",
            "cells": [{"column_key": "estimate", "evidence_field_id": field_id, "missingness_reason": None}],
            "expected_revision": project["revision"],
            "idempotency_key": "api-row",
        },
    ).json()
    exported = client.post(
        f"/v1/projects/{project_id}/evidence-tables/{table_id}/exports",
        json={
            "format": "json",
            "expected_revision": project["revision"],
            "idempotency_key": "api-export",
        },
    )
    assert exported.status_code == 201
    export_id = exported.json()["id"]
    manifest_response = client.get(f"/v1/project-exports/{export_id}/manifest")
    assert manifest_response.status_code == 200, manifest_response.text
    assert client.get(f"/v1/project-exports/{export_id}/data").json()[0]["work_id"] == "work_research"
    assert client.get(f"/v1/project-exports/{export_id}/unknown").status_code == 404
    denied = client.get(f"/v1/projects/{project_id}", actor="outsider")
    assert denied.status_code == 403
    assert denied.json()["code"] == "project_authorization_denied"


def test_project_api_requires_step4_composition(memory_system: object) -> None:
    """A Step 1-only container reports a stable configuration error for projects."""
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    response = client.get("/v1/projects")
    assert response.status_code == 400
    assert response.json()["code"] == "configuration_error"


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: Project(
                "p",
                "",
                "",
                "owner",
                _NOW,
                None,
                None,
                "key",
            ),
            "project name",
        ),
        (
            lambda: Project(
                "p",
                "name",
                "",
                "owner",
                _NOW,
                "snapshot",
                None,
                "key",
            ),
            "must be paired",
        ),
        (
            lambda: EvidenceTableCell("estimate", None, None),
            "exactly one",
        ),
        (
            lambda: ProjectEvidenceAnchor("e", "p1", "quote", "bad"),
            "SHA-256",
        ),
        (
            lambda: ProjectEvidenceField(
                "f",
                "estimate",
                "value",
                1,
                None,
                "all",
                None,
                EvidenceState.SUGGESTED,
                EvidenceLineageMethod.USER_AUTHORED,
                1,
                (ProjectEvidenceAnchor("e", "p1", "quote", _SHA_A),),
                "owner",
                _NOW,
            ),
            "must not imply",
        ),
    ],
)
def test_project_domain_rejects_ambiguous_or_unattributed_state(factory: object, message: str) -> None:
    """Critical Step 4 domain invariants reject ambiguous analytical state."""
    with pytest.raises(EconomicEvidenceGraphError, match=message):
        cast(Any, factory)()


def test_project_domain_guards_all_analytical_boundaries() -> None:
    """Size, state, uniqueness, source-support, and temporal invariants are executable."""
    anchor = ProjectEvidenceAnchor("evidence", "paragraph:1", "quote", _SHA_A)
    field = ProjectEvidenceField(
        "field",
        "estimate",
        "1.0",
        1.0,
        "percent",
        "baseline",
        None,
        EvidenceState.OBSERVED,
        EvidenceLineageMethod.OBSERVED_TRANSCRIPTION,
        1,
        (anchor,),
        "owner",
        _NOW,
    )
    with pytest.raises(EconomicEvidenceGraphError, match="size limit"):
        replace(_create_project(_system()), name="x" * 201)
    with pytest.raises(EconomicEvidenceGraphError, match="revision"):
        replace(_create_project(_system()), revision=0)
    with pytest.raises(EconomicEvidenceGraphError, match="2000"):
        ScreeningEvent(
            "screen",
            "project",
            "work",
            None,
            ScreeningOutcome.INCLUDE,
            None,
            "x" * 2_001,
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="10000"):
        ProjectNote("note", "project", "work", "work", "x" * 10_001, "owner", _NOW, "key")
    with pytest.raises(EconomicEvidenceGraphError, match="delivery limit"):
        ProjectEvidenceAnchor("evidence", "paragraph:1", "x" * 601, _SHA_A)
    with pytest.raises(EconomicEvidenceGraphError, match="between zero and one"):
        replace(field, confidence=1.1)
    with pytest.raises(EconomicEvidenceGraphError, match="display value"):
        replace(field, display_value="")
    with pytest.raises(EconomicEvidenceGraphError, match="lowercase machine code"):
        replace(field, missingness_reason="Not Reported")
    with pytest.raises(EconomicEvidenceGraphError, match="require source anchors"):
        replace(field, anchors=())
    with pytest.raises(EconomicEvidenceGraphError, match="unique"):
        replace(field, anchors=(anchor, anchor))
    with pytest.raises(EconomicEvidenceGraphError, match="require unique fields"):
        ProjectEvidenceRecord(
            "record",
            "project",
            "work",
            "version",
            "estimate",
            "v1",
            (),
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="require unique fields"):
        ProjectEvidenceRecord(
            "record",
            "project",
            "work",
            "version",
            "estimate",
            "v1",
            (field, replace(field, id="field_2")),
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="unsupported"):
        EvidenceTableColumn("estimate", "Estimate", "object")
    column = EvidenceTableColumn("estimate", "Estimate", "number")
    with pytest.raises(EconomicEvidenceGraphError, match="unique columns"):
        EvidenceTable("table", "project", "name", "", (column, column), "owner", _NOW, "key")
    with pytest.raises(EconomicEvidenceGraphError, match="at most 50"):
        EvidenceTable(
            "table",
            "project",
            "name",
            "",
            tuple(EvidenceTableColumn(f"field_{index}", str(index), "text") for index in range(51)),
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="exactly one"):
        EvidenceTableCell("estimate", "field", "not_reported")
    cell = EvidenceTableCell("estimate", "field", None)
    with pytest.raises(EconomicEvidenceGraphError, match="unique populated"):
        EvidenceTableRow("row", "project", "table", "record", "label", (cell, cell), "owner", _NOW, "key")
    with pytest.raises(EconomicEvidenceGraphError, match="distinct"):
        PolicyLink(
            "link",
            "project",
            "work",
            "work",
            PolicyLinkType.TOPICAL_SUGGESTION,
            None,
            "manual_review",
            1,
            EvidenceState.SUGGESTED,
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="confidence"):
        PolicyLink(
            "link",
            "project",
            "research",
            "policy",
            PolicyLinkType.TOPICAL_SUGGESTION,
            None,
            "manual_review",
            -1,
            EvidenceState.SUGGESTED,
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="require exact evidence"):
        PolicyLink(
            "link",
            "project",
            "research",
            "policy",
            PolicyLinkType.DIRECT_CITATION,
            None,
            "manual_review",
            1,
            EvidenceState.OBSERVED,
            "owner",
            _NOW,
            "key",
        )
    with pytest.raises(EconomicEvidenceGraphError, match="must change"):
        ReviewEvent(
            "review",
            "project",
            ReviewTargetType.EVIDENCE_FIELD,
            "field",
            EvidenceState.SUGGESTED,
            EvidenceState.SUGGESTED,
            "reason",
            "reviewer",
            _NOW,
            "key",
        )
    valid_export = ProjectExport(
        "export",
        "project",
        "table",
        ProjectExportFormat.JSON,
        1,
        "memory://data",
        _SHA_A,
        "application/json",
        1,
        "memory://manifest",
        _SHA_B,
        1,
        (),
        "owner",
        _NOW,
        "key",
    )
    with pytest.raises(EconomicEvidenceGraphError, match="hashes"):
        replace(valid_export, data_sha256="bad")
    with pytest.raises(EconomicEvidenceGraphError, match="byte length"):
        replace(valid_export, data_byte_length=-1)
    project = _create_project(_system())
    wrong_member = ProjectMember("member", "other", "owner", ProjectRole.OWNER, "owner", _NOW, "key")
    with pytest.raises(EconomicEvidenceGraphError, match="cross-project"):
        ProjectBundle(project, (wrong_member,), (), (), (), (), (), (), (), (), ())
    with pytest.raises(EconomicEvidenceGraphError, match="timezone-aware"):
        replace(project, created_at=datetime(2026, 8, 5))


def test_review_state_detects_broken_history() -> None:
    """Review folding fails when stored prior-state lineage is inconsistent."""
    event = ReviewEvent(
        id="rev",
        project_id="project",
        target_type=ReviewTargetType.EVIDENCE_FIELD,
        target_id="field",
        previous_state=EvidenceState.UNCERTAIN,
        new_state=EvidenceState.APPROVED,
        reason="bad history",
        reviewed_by="reviewer",
        reviewed_at=_NOW,
        idempotency_key="review",
    )
    with pytest.raises(EconomicEvidenceGraphError, match="broken"):
        review_state(EvidenceState.SUGGESTED, (event,))
    assert HistoricalMode.STRICT.value == "strict"
