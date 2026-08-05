"""FastAPI application factory for the versioned local HTTP surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Header, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from economic_evidence_graph.api.project_schemas import (
    EvidenceRecordCreateRequest,
    EvidenceTableCreateRequest,
    EvidenceTableRowCreateRequest,
    PolicyLinkCreateRequest,
    ProjectBundleResource,
    ProjectCreateRequest,
    ProjectExportCreateRequest,
    ProjectExportResource,
    ProjectMemberCreateRequest,
    ProjectNoteCreateRequest,
    ProjectResource,
    ProjectSearchRunCreateRequest,
    ReviewCreateRequest,
    ScreeningCreateRequest,
)
from economic_evidence_graph.api.schemas import (
    EvidenceSnippetResource,
    ExportCreateRequest,
    HealthResponse,
    JobResource,
    ProblemDetail,
    SearchCreateRequest,
    SearchResponseResource,
    SearchRunResource,
    WorkResponse,
)
from economic_evidence_graph.api.tool_schemas import (
    AgentToolCallResource,
    AgentToolDefinitionResource,
    ToolCompareEvidenceRequest,
    ToolCompareVersionsRequest,
    ToolExportProjectRequest,
    ToolGetWorkRequest,
    ToolRetrieveEvidenceRequest,
    ToolSearchLiteratureRequest,
    ToolTraceRequest,
)
from economic_evidence_graph.api.ui import PROJECTS_HTML, WORKBENCH_HTML
from economic_evidence_graph.application import (
    AgentToolService,
    EvidenceAnchorInput,
    EvidenceFieldInput,
    ExportRequest,
    ProjectService,
    RetrievalService,
    TableCellInput,
)
from economic_evidence_graph.bootstrap import Container, create_local_container
from economic_evidence_graph.config import Settings
from economic_evidence_graph.domain import (
    EvidenceLineageMethod,
    EvidenceState,
    EvidenceTableColumn,
    HistoricalCutoff,
    HistoricalMode,
    JobState,
    PolicyLinkType,
    ProjectExportFormat,
    ProjectRole,
    ReviewTargetType,
    ScreeningOutcome,
    SearchFilters,
    SearchMode,
    SearchRequest,
)
from economic_evidence_graph.exceptions import (
    AuthorizationError,
    ConflictError,
    EconomicEvidenceGraphError,
    NotFoundError,
    SearchBackendError,
)
from economic_evidence_graph.ports import ArtifactRef
from economic_evidence_graph.serialization import to_json_value


def create_app(container: Container | None = None) -> FastAPI:
    """Create the versioned API with an explicit or local dependency container."""
    dependencies = container or create_local_container(Settings.from_environment())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Release process-owned provider pools on application shutdown."""
        try:
            yield
        finally:
            if container is None:
                dependencies.close()

    app = FastAPI(
        title="Economic Evidence Graph API",
        summary="Versioned, auditable economic evidence infrastructure",
        version="1.0.0-alpha.1",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.container = dependencies

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def workbench() -> str:
        """Serve the dependency-free local economist workbench."""
        return WORKBENCH_HTML

    @app.get("/projects", response_class=HTMLResponse, include_in_schema=False)
    def projects_workbench() -> str:
        """Serve the dependency-free project and evidence-table workbench."""
        return PROJECTS_HTML

    @app.middleware("http")
    async def request_identity(request: Request, call_next: object) -> Response:
        """Attach a bounded request identifier to every response."""
        requested = request.headers.get("X-Request-ID", "")
        request_id = requested if 0 < len(requested) <= 128 else uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(EconomicEvidenceGraphError)
    async def application_error(request: Request, error: EconomicEvidenceGraphError) -> JSONResponse:
        """Translate expected application failures into problem details."""
        error_status = _status_for(error)
        problem = ProblemDetail(
            type=f"https://economic-evidence-graph.invalid/problems/{error.code}",
            title=_title_for(error_status),
            status=error_status,
            detail=str(error),
            instance=request.url.path,
            code=error.code,
            request_id=_request_id(request),
            retryable=isinstance(error, SearchBackendError),
            context=dict(error.context),
        )
        return JSONResponse(
            status_code=error_status,
            content=problem.model_dump(mode="json"),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        """Return request validation failures through the same error contract."""
        problem = ProblemDetail(
            type="https://economic-evidence-graph.invalid/problems/request_validation_error",
            title="Unprocessable Content",
            # The symbolic constant was renamed between supported Starlette versions.
            status=422,
            detail="request validation failed",
            instance=request.url.path,
            code="request_validation_error",
            request_id=_request_id(request),
            retryable=False,
            errors=jsonable_encoder(error.errors()),
        )
        return JSONResponse(
            status_code=problem.status,
            content=problem.model_dump(mode="json"),
            media_type="application/problem+json",
        )

    @app.get("/health/live", response_model=HealthResponse, tags=["operations"])
    def live() -> HealthResponse:
        """Report process liveness without touching dependencies."""
        return HealthResponse()

    @app.get("/health/ready", response_model=HealthResponse, tags=["operations"])
    def ready() -> HealthResponse:
        """Report readiness after checking canonical storage providers."""
        dependencies.catalog_repository.check()
        dependencies.artifacts.check()
        if dependencies.search_index is not None:
            dependencies.search_index.check()
        return HealthResponse()

    @app.post("/v1/search", response_model=SearchResponseResource, tags=["search"])
    def search(body: SearchCreateRequest) -> SearchResponseResource:
        """Run strict, explainable hybrid retrieval over the active generation."""
        service = _retrieval(dependencies)
        cutoff = (
            None
            if body.historical_cutoff is None
            else HistoricalCutoff(
                at=body.historical_cutoff.at,
                mode=HistoricalMode(body.historical_cutoff.mode),
            )
        )
        result = service.search(
            SearchRequest(
                query=body.query,
                modes=tuple(SearchMode(item) for item in body.modes),
                filters=SearchFilters(
                    source_codes=tuple(body.filters.source_codes),
                    published_from=body.filters.published_from,
                    published_to=body.filters.published_to,
                ),
                historical_cutoff=cutoff,
                collapse_versions=body.collapse_versions,
                limit=body.limit,
                cursor=body.cursor,
                save_run=body.save_run,
            )
        )
        return SearchResponseResource.from_domain(result)

    @app.get("/v1/search/runs/{run_id}", response_model=SearchRunResource, tags=["search"])
    def get_search_run(run_id: str) -> SearchRunResource:
        """Return a saved request and exact response manifest."""
        return SearchRunResource.from_domain(_retrieval(dependencies).saved_run(run_id))

    @app.post("/v1/search/runs/{run_id}/replay", response_model=SearchResponseResource, tags=["search"])
    def replay_search_run(run_id: str) -> SearchResponseResource:
        """Replay a saved result exactly without silently using a newer generation."""
        run = _retrieval(dependencies).saved_run(run_id)
        return SearchResponseResource.model_validate(run.response_payload)

    @app.get("/v1/evidence/{evidence_unit_id}", response_model=EvidenceSnippetResource, tags=["evidence"])
    def get_evidence(evidence_unit_id: str) -> EvidenceSnippetResource:
        """Return one current, policy-gated evidence excerpt and its lineage."""
        return EvidenceSnippetResource.from_domain(_retrieval(dependencies).evidence(evidence_unit_id))

    @app.get("/v1/works/{work_id}", response_model=WorkResponse, tags=["works"])
    def get_work(work_id: str) -> WorkResponse:
        """Return a provenance-complete canonical work."""
        return WorkResponse.from_bundle(dependencies.catalog.get_work(work_id))

    @app.get("/v1/agent-tools", response_model=list[AgentToolDefinitionResource], tags=["agent-tools"])
    def agent_tool_definitions() -> list[AgentToolDefinitionResource]:
        """Publish the exact bounded input schemas accepted by research agents."""
        definitions = (
            (
                "search_literature",
                "Search literature with explicit cutoff and provenance.",
                ToolSearchLiteratureRequest,
            ),
            ("get_work", "Get canonical work metadata and version projections.", ToolGetWorkRequest),
            ("retrieve_evidence", "Retrieve one verbatim policy-gated evidence excerpt.", ToolRetrieveEvidenceRequest),
            ("compare_evidence", "Read an authorized, policy-revalidated evidence table.", ToolCompareEvidenceRequest),
            (
                "compare_versions",
                "Compare bounded textual sections across two work versions.",
                ToolCompareVersionsRequest,
            ),
            ("trace_citations", "Traverse only direct-citation project relationships.", ToolTraceRequest),
            ("trace_policy_links", "Traverse typed policy relationships without implying influence.", ToolTraceRequest),
            ("export_project", "Create a governed project table export and manifest.", ToolExportProjectRequest),
        )
        return [
            AgentToolDefinitionResource(
                name=name,
                description=description,
                input_schema=model.model_json_schema(mode="validation"),
            )
            for name, description, model in definitions
        ]

    @app.post(
        "/v1/agent-tools/search_literature",
        response_model=AgentToolCallResource,
        tags=["agent-tools"],
    )
    def tool_search_literature(
        body: ToolSearchLiteratureRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Run bounded, provenance-complete hybrid retrieval as a typed tool."""
        cutoff = (
            None
            if body.historical_cutoff is None
            else HistoricalCutoff(at=body.historical_cutoff.at, mode=HistoricalMode(body.historical_cutoff.mode))
        )
        result = _tools(dependencies).search_literature(
            SearchRequest(
                query=body.query,
                modes=tuple(SearchMode(item) for item in body.modes),
                filters=SearchFilters(
                    source_codes=tuple(body.filters.source_codes),
                    published_from=body.filters.published_from,
                    published_to=body.filters.published_to,
                ),
                historical_cutoff=cutoff,
                collapse_versions=body.collapse_versions,
                limit=body.limit,
                cursor=body.cursor,
                save_run=body.save_run,
            ),
            actor_id=actor_id,
        )
        return AgentToolCallResource.from_domain(result)

    @app.post("/v1/agent-tools/get_work", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_get_work(
        body: ToolGetWorkRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Get one exact work from the active search generation."""
        return AgentToolCallResource.from_domain(_tools(dependencies).get_work(body.work_id, actor_id=actor_id))

    @app.post("/v1/agent-tools/retrieve_evidence", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_retrieve_evidence(
        body: ToolRetrieveEvidenceRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Retrieve one policy-gated excerpt without invoking generation."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).retrieve_evidence(body.evidence_unit_id, actor_id=actor_id)
        )

    @app.post("/v1/agent-tools/compare_evidence", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_compare_evidence(
        body: ToolCompareEvidenceRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Return an authorized evidence table with current delivery checks."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).compare_evidence(body.project_id, body.table_id, actor_id=actor_id)
        )

    @app.post("/v1/agent-tools/compare_versions", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_compare_versions(
        body: ToolCompareVersionsRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Compare bounded version evidence without inferring substantive change."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).compare_versions(
                body.work_id,
                body.left_document_version_id,
                body.right_document_version_id,
                actor_id=actor_id,
                include_unchanged=body.include_unchanged,
                limit=body.limit,
            )
        )

    @app.post("/v1/agent-tools/trace_citations", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_trace_citations(
        body: ToolTraceRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Trace direct citations under project authorization and hard bounds."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).trace_citations(
                body.project_id,
                body.work_id,
                actor_id=actor_id,
                depth=body.depth,
                node_limit=body.node_limit,
                edge_limit=body.edge_limit,
            )
        )

    @app.post("/v1/agent-tools/trace_policy_links", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_trace_policy_links(
        body: ToolTraceRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Trace typed policy links under project authorization and hard bounds."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).trace_policy_links(
                body.project_id,
                body.work_id,
                actor_id=actor_id,
                depth=body.depth,
                node_limit=body.node_limit,
                edge_limit=body.edge_limit,
            )
        )

    @app.post("/v1/agent-tools/export_project", response_model=AgentToolCallResource, tags=["agent-tools"])
    def tool_export_project(
        body: ToolExportProjectRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> AgentToolCallResource:
        """Create a governed project export without returning unbounded artifact data."""
        return AgentToolCallResource.from_domain(
            _tools(dependencies).export_project(
                body.project_id,
                body.table_id,
                actor_id=actor_id,
                format=ProjectExportFormat(body.format),
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects", response_model=ProjectResource, status_code=status.HTTP_201_CREATED, tags=["projects"])
    def create_project(
        body: ProjectCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Create an attributed local research project."""
        return ProjectResource.from_domain(
            _projects(dependencies).create(
                name=body.name,
                description=body.description,
                actor_id=actor_id,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.get("/v1/projects", response_model=list[ProjectResource], tags=["projects"])
    def list_projects(
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> list[ProjectResource]:
        """List projects visible through explicit membership."""
        return [ProjectResource.from_domain(item) for item in _projects(dependencies).list(actor_id=actor_id)]

    @app.get("/v1/projects/{project_id}", response_model=ProjectBundleResource, tags=["projects"])
    def get_project(
        project_id: str,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectBundleResource:
        """Return a complete, authorized project handoff."""
        return ProjectBundleResource.from_domain(_projects(dependencies).get(project_id, actor_id=actor_id))

    @app.post("/v1/projects/{project_id}/members", response_model=ProjectResource, tags=["projects"])
    def add_project_member(
        project_id: str,
        body: ProjectMemberCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Add a project collaborator as an owner."""
        return ProjectResource.from_domain(
            _projects(dependencies).add_member(
                project_id,
                actor_id=actor_id,
                member_actor_id=body.actor_id,
                role=ProjectRole(body.role),
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects/{project_id}/search-runs", response_model=ProjectResource, tags=["projects"])
    def attach_project_search_run(
        project_id: str,
        body: ProjectSearchRunCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Attach an exact saved search manifest."""
        return ProjectResource.from_domain(
            _projects(dependencies).attach_search_run(
                project_id,
                actor_id=actor_id,
                search_run_id=body.search_run_id,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects/{project_id}/screening", response_model=ProjectResource, tags=["projects"])
    def screen_project_work(
        project_id: str,
        body: ScreeningCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Append a screening decision without erasing prior history."""
        return ProjectResource.from_domain(
            _projects(dependencies).screen(
                project_id,
                actor_id=actor_id,
                work_id=body.work_id,
                document_version_id=body.document_version_id,
                outcome=ScreeningOutcome(body.outcome),
                reason_code=body.reason_code,
                note=body.note,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects/{project_id}/notes", response_model=ProjectResource, tags=["projects"])
    def add_project_note(
        project_id: str,
        body: ProjectNoteCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Append an attributed project note."""
        return ProjectResource.from_domain(
            _projects(dependencies).add_note(
                project_id,
                actor_id=actor_id,
                target_type=body.target_type,
                target_id=body.target_id,
                body=body.body,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post(
        "/v1/projects/{project_id}/evidence-records",
        response_model=ProjectResource,
        tags=["evidence"],
    )
    def add_project_evidence_record(
        project_id: str,
        body: EvidenceRecordCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Create a typed evidence record after validating every source quote."""
        fields = tuple(
            EvidenceFieldInput(
                field_name=item.field_name,
                display_value=item.display_value,
                normalized_value=item.normalized_value,
                units=item.units,
                applicability=item.applicability,
                missingness_reason=item.missingness_reason,
                state=EvidenceState(item.state),
                lineage_method=EvidenceLineageMethod(item.lineage_method),
                confidence=item.confidence,
                anchors=tuple(EvidenceAnchorInput(anchor.evidence_unit_id, anchor.quote) for anchor in item.anchors),
            )
            for item in body.fields
        )
        return ProjectResource.from_domain(
            _projects(dependencies).add_evidence_record(
                project_id,
                actor_id=actor_id,
                work_id=body.work_id,
                document_version_id=body.document_version_id,
                record_type=body.record_type,
                fields=fields,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects/{project_id}/evidence-tables", response_model=ProjectResource, tags=["evidence"])
    def create_evidence_table(
        project_id: str,
        body: EvidenceTableCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Create a declared evidence-comparison schema."""
        columns = tuple(EvidenceTableColumn(item.key, item.label, item.value_type, item.units) for item in body.columns)
        return ProjectResource.from_domain(
            _projects(dependencies).create_evidence_table(
                project_id,
                actor_id=actor_id,
                name=body.name,
                description=body.description,
                columns=columns,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post(
        "/v1/projects/{project_id}/evidence-tables/{table_id}/rows",
        response_model=ProjectResource,
        tags=["evidence"],
    )
    def add_evidence_table_row(
        project_id: str,
        table_id: str,
        body: EvidenceTableRowCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Add a comparison row with explicit values or missingness."""
        return ProjectResource.from_domain(
            _projects(dependencies).add_evidence_table_row(
                project_id,
                table_id,
                actor_id=actor_id,
                evidence_record_id=body.evidence_record_id,
                label=body.label,
                cells=tuple(
                    TableCellInput(item.column_key, item.evidence_field_id, item.missingness_reason)
                    for item in body.cells
                ),
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post("/v1/projects/{project_id}/policy-links", response_model=ProjectResource, tags=["evidence"])
    def add_policy_link(
        project_id: str,
        body: PolicyLinkCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Create a typed research-to-policy relationship."""
        anchor = None if body.anchor is None else EvidenceAnchorInput(body.anchor.evidence_unit_id, body.anchor.quote)
        return ProjectResource.from_domain(
            _projects(dependencies).add_policy_link(
                project_id,
                actor_id=actor_id,
                research_work_id=body.research_work_id,
                policy_work_id=body.policy_work_id,
                relationship_type=PolicyLinkType(body.relationship_type),
                anchor=anchor,
                method=body.method,
                confidence=body.confidence,
                state=EvidenceState(body.state),
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.get("/v1/projects/{project_id}/policy-links/{work_id}", tags=["evidence"])
    def trace_policy_links(
        project_id: str,
        work_id: str,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> list[object]:
        """Trace bounded typed policy adjacency without implying influence."""
        payload = to_json_value(
            _projects(dependencies).trace_policy_links(project_id, actor_id=actor_id, work_id=work_id)
        )
        return cast(list[object], payload)

    @app.post("/v1/projects/{project_id}/reviews", response_model=ProjectResource, tags=["evidence"])
    def review_project_evidence(
        project_id: str,
        body: ReviewCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectResource:
        """Append an owner/reviewer decision."""
        return ProjectResource.from_domain(
            _projects(dependencies).review(
                project_id,
                actor_id=actor_id,
                target_type=ReviewTargetType(body.target_type),
                target_id=body.target_id,
                new_state=EvidenceState(body.new_state),
                reason=body.reason,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.post(
        "/v1/projects/{project_id}/evidence-tables/{table_id}/exports",
        response_model=ProjectExportResource,
        status_code=status.HTTP_201_CREATED,
        tags=["exports"],
    )
    def export_evidence_table(
        project_id: str,
        table_id: str,
        body: ProjectExportCreateRequest,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> ProjectExportResource:
        """Create a governed open-format table export and manifest."""
        return ProjectExportResource.from_domain(
            _projects(dependencies).export_table(
                project_id,
                table_id,
                actor_id=actor_id,
                format=ProjectExportFormat(body.format),
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.get("/v1/project-exports/{export_id}/{artifact_kind}", tags=["exports"])
    def get_project_export_artifact(
        export_id: str,
        artifact_kind: str,
        actor_id: str = Header(default="local-user", alias="X-EEG-Actor"),
    ) -> Response:
        """Download an authorized project data file or reproducibility manifest."""
        if artifact_kind not in {"data", "manifest"}:
            raise NotFoundError("project export artifact was not found")
        ref, _ = _projects(dependencies).export_artifact(
            export_id, actor_id=actor_id, manifest=artifact_kind == "manifest"
        )
        return Response(
            content=dependencies.artifacts.get_bytes(ref),
            media_type=ref.media_type,
            headers={"ETag": f'"sha256:{ref.sha256}"'},
        )

    @app.post(
        "/v1/exports",
        response_model=JobResource,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["exports"],
    )
    def create_export(body: ExportCreateRequest) -> JobResource:
        """Create an idempotent evidence-pack export job."""
        job = dependencies.jobs.submit_export(
            request=ExportRequest(
                work_ids=tuple(body.work_ids),
                snapshot_id=body.snapshot_id,
                format=body.format,
            ),
            idempotency_key=body.idempotency_key,
        )
        return JobResource.from_domain(job)

    @app.get("/v1/jobs/{job_id}", response_model=JobResource, tags=["jobs"])
    def get_job(job_id: str) -> JobResource:
        """Return durable job state."""
        return JobResource.from_domain(dependencies.jobs.get(job_id))

    @app.get("/v1/exports/{job_id}/manifest", tags=["exports"])
    def get_export_manifest(job_id: str) -> Response:
        """Return a completed export manifest after job authorization."""
        job = dependencies.jobs.get(job_id)
        if job.kind != "evidence_pack_export" or job.state is not JobState.SUCCEEDED or job.output is None:
            raise ConflictError("export manifest is not available", context={"job_id": job_id})
        ref = ArtifactRef(
            uri=str(job.output["manifest_uri"]),
            sha256=str(job.output["manifest_sha256"]),
            media_type="application/json",
            byte_length=cast(int, job.output["manifest_byte_length"]),
        )
        return Response(
            content=dependencies.artifacts.get_bytes(ref),
            media_type="application/json",
            headers={"ETag": f'"sha256:{ref.sha256}"'},
        )

    return app


def _status_for(error: EconomicEvidenceGraphError) -> int:
    """Map stable exception types to HTTP status codes."""
    if isinstance(error, NotFoundError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(error, ConflictError):
        return status.HTTP_409_CONFLICT
    if isinstance(error, AuthorizationError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(error, SearchBackendError):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_400_BAD_REQUEST


def _title_for(error_status: int) -> str:
    """Return a stable problem title for an HTTP status."""
    return {
        status.HTTP_400_BAD_REQUEST: "Bad Request",
        status.HTTP_404_NOT_FOUND: "Not Found",
        status.HTTP_409_CONFLICT: "Conflict",
        status.HTTP_503_SERVICE_UNAVAILABLE: "Service Unavailable",
    }.get(error_status, "Application Error")


def _request_id(request: Request) -> str:
    """Read the request identifier even if middleware initialization failed."""
    return str(getattr(request.state, "request_id", "unknown"))


def _retrieval(container: Container) -> RetrievalService:
    """Require the optional search service while preserving isolated Step 1 tests."""
    from economic_evidence_graph.exceptions import ConfigurationError

    if not isinstance(container.retrieval, RetrievalService):
        raise ConfigurationError("search is not configured in this application container")
    return container.retrieval


def _projects(container: Container) -> ProjectService:
    """Require the Step 4 project service in the active composition."""
    from economic_evidence_graph.exceptions import ConfigurationError

    if not isinstance(container.projects, ProjectService):
        raise ConfigurationError("projects are not configured in this application container")
    return container.projects


def _tools(container: Container) -> AgentToolService:
    """Require the Step 5 agent-tool service in the active composition."""
    from economic_evidence_graph.exceptions import ConfigurationError

    if not isinstance(container.tools, AgentToolService):
        raise ConfigurationError("agent tools are not configured in this application container")
    return container.tools
