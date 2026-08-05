"""Typed Python client for the versioned Economic Evidence Graph API."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from time import monotonic, sleep
from typing import TypeVar, cast

import httpx
from pydantic import BaseModel

from economic_evidence_graph.api.project_schemas import (
    ProjectBundleResource,
    ProjectCreateRequest,
    ProjectExportResource,
    ProjectResource,
)
from economic_evidence_graph.api.schemas import (
    EvidenceSnippetResource,
    JobResource,
    ProblemDetail,
    SearchCreateRequest,
    SearchResponseResource,
    SearchResultResource,
    WorkResponse,
)
from economic_evidence_graph.api.tool_schemas import AgentToolCallResource
from economic_evidence_graph.serialization import JsonValue

ModelT = TypeVar("ModelT", bound=BaseModel)
_TOOL_NAMES = frozenset(
    {
        "search_literature",
        "get_work",
        "retrieve_evidence",
        "compare_evidence",
        "compare_versions",
        "trace_citations",
        "trace_policy_links",
        "export_project",
    }
)


class EegClientError(RuntimeError):
    """Structured remote API failure safe for programmatic handling."""

    def __init__(self, problem: ProblemDetail) -> None:
        super().__init__(problem.detail)
        self.status = problem.status
        self.code = problem.code
        self.request_id = problem.request_id
        self.retryable = problem.retryable
        self.context = problem.context


class EegClient:
    """Synchronous typed client with pagination and project/export helpers.

    Args:
        base_url: API origin, normally ``http://127.0.0.1:8000`` locally.
        actor_id: Explicit local actor attribution sent on every request.
        timeout: Network timeout in seconds when constructing an HTTP client.
        http_client: Optional injected client for testing or custom transports.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        actor_id: str = "local-user",
        timeout: float = 30,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not actor_id.strip() or len(actor_id) > 255:
            raise ValueError("actor_id must be between 1 and 255 characters")
        self._client = http_client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._owns_client = http_client is None
        self._actor_id = actor_id

    def close(self) -> None:
        """Close a client created by this facade."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EegClient:
        """Return this client as a context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Release owned network resources."""
        self.close()

    def search(self, request: SearchCreateRequest) -> SearchResponseResource:
        """Run one typed hybrid search page."""
        return self._model("POST", "/v1/search", SearchResponseResource, json=request.model_dump(mode="json"))

    def iter_search(
        self,
        request: SearchCreateRequest,
        *,
        max_pages: int = 100,
    ) -> Iterator[SearchResultResource]:
        """Yield search pages under an explicit client-side safety cap."""
        if not 1 <= max_pages <= 1_000:
            raise ValueError("max_pages must be between 1 and 1,000")
        current = request
        for _ in range(max_pages):
            page = self.search(current)
            yield from page.results
            if page.next_cursor is None:
                return
            current = current.model_copy(update={"cursor": page.next_cursor, "save_run": False})
        raise RuntimeError("search exceeded max_pages before cursor exhaustion")

    def get_work(self, work_id: str) -> WorkResponse:
        """Return a provenance-complete canonical work bundle."""
        return self._model("GET", f"/v1/works/{work_id}", WorkResponse)

    def retrieve_evidence(self, evidence_unit_id: str) -> EvidenceSnippetResource:
        """Return one current policy-gated evidence excerpt."""
        return self._model("GET", f"/v1/evidence/{evidence_unit_id}", EvidenceSnippetResource)

    def create_project(self, request: ProjectCreateRequest) -> ProjectResource:
        """Create an attributed research project."""
        return self._model("POST", "/v1/projects", ProjectResource, json=request.model_dump(mode="json"))

    def list_projects(self) -> list[ProjectResource]:
        """List projects visible to the configured actor."""
        payload = self._json("GET", "/v1/projects")
        if not isinstance(payload, list):
            raise TypeError("project list response must be an array")
        return [ProjectResource.model_validate(item) for item in payload]

    def get_project(self, project_id: str) -> ProjectBundleResource:
        """Return one complete authorized project handoff."""
        return self._model("GET", f"/v1/projects/{project_id}", ProjectBundleResource)

    def call_tool(self, tool_name: str, arguments: Mapping[str, JsonValue]) -> AgentToolCallResource:
        """Call one allowlisted typed research tool."""
        if tool_name not in _TOOL_NAMES:
            raise ValueError(f"unknown agent tool: {tool_name}")
        return self._model(
            "POST",
            f"/v1/agent-tools/{tool_name}",
            AgentToolCallResource,
            json=dict(arguments),
        )

    def compare_evidence(self, project_id: str, table_id: str) -> AgentToolCallResource:
        """Preview a project evidence table with current source-policy checks."""
        return self.call_tool("compare_evidence", {"project_id": project_id, "table_id": table_id})

    def export_project(
        self,
        project_id: str,
        table_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        format: str = "json",
    ) -> ProjectExportResource:
        """Create a governed export through the shared agent-tool authorization path."""
        call = self.call_tool(
            "export_project",
            {
                "project_id": project_id,
                "table_id": table_id,
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
                "format": format,
            },
        )
        return ProjectExportResource.model_validate(call.result)

    def download_project_export(self, export_id: str, *, manifest: bool = False) -> bytes:
        """Download an authorized project export artifact."""
        kind = "manifest" if manifest else "data"
        return self._bytes("GET", f"/v1/project-exports/{export_id}/{kind}")

    def get_job(self, job_id: str) -> JobResource:
        """Return one durable asynchronous job."""
        return self._model("GET", f"/v1/jobs/{job_id}", JobResource)

    def wait_for_job(self, job_id: str, *, timeout: float = 60, poll_interval: float = 0.25) -> JobResource:
        """Poll a job to a terminal state under an explicit client timeout."""
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")
        deadline = monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.state in {"succeeded", "failed"}:
                return job
            if monotonic() >= deadline:
                raise TimeoutError(f"job did not finish within {timeout} seconds: {job_id}")
            sleep(min(poll_interval, max(0, deadline - monotonic())))

    def _model(
        self,
        method: str,
        path: str,
        model: type[ModelT],
        *,
        json: object | None = None,
    ) -> ModelT:
        """Request and validate one typed JSON resource."""
        return model.model_validate(self._json(method, path, json=json))

    def _json(self, method: str, path: str, *, json: object | None = None) -> object:
        """Request one JSON payload through the shared error contract."""
        response = self._request(method, path, json=json)
        try:
            return cast(object, response.json())
        except ValueError as error:
            raise RuntimeError("Economic Evidence Graph returned invalid JSON") from error

    def _bytes(self, method: str, path: str) -> bytes:
        """Request one checked binary artifact."""
        return self._request(method, path).content

    def _request(self, method: str, path: str, *, json: object | None = None) -> httpx.Response:
        """Apply attribution and normalize RFC 9457 errors."""
        response = self._client.request(
            method,
            path,
            json=json,
            headers={"X-EEG-Actor": self._actor_id},
        )
        if response.is_error:
            try:
                problem = ProblemDetail.model_validate(response.json())
            except (ValueError, TypeError) as error:
                response.raise_for_status()
                raise RuntimeError("unreachable") from error  # pragma: no cover
            raise EegClientError(problem)
        return response
