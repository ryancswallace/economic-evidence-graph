"""Strict HTTP contracts for the eight bounded agent tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from economic_evidence_graph.api.schemas import HistoricalCutoffRequest, SearchFiltersRequest, StrictModel
from economic_evidence_graph.application import ToolCallResult
from economic_evidence_graph.serialization import to_json_value


class ToolSearchLiteratureRequest(StrictModel):
    """Bounded hybrid-retrieval arguments for agents."""

    query: str = Field(min_length=1, max_length=500)
    modes: list[Literal["lexical", "semantic", "structured"]] = Field(
        default_factory=lambda: ["lexical", "semantic"], min_length=1, max_length=3
    )
    filters: SearchFiltersRequest = Field(default_factory=SearchFiltersRequest)
    historical_cutoff: HistoricalCutoffRequest | None = None
    collapse_versions: bool = True
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=2048)
    save_run: bool = False


class ToolGetWorkRequest(StrictModel):
    """Exact canonical-work lookup arguments."""

    work_id: str = Field(min_length=1, max_length=64)


class ToolRetrieveEvidenceRequest(StrictModel):
    """Exact evidence-unit lookup arguments."""

    evidence_unit_id: str = Field(min_length=1, max_length=64)


class ToolCompareEvidenceRequest(StrictModel):
    """Authorized evidence-table preview arguments."""

    project_id: str = Field(min_length=1, max_length=64)
    table_id: str = Field(min_length=1, max_length=64)


class ToolCompareVersionsRequest(StrictModel):
    """Bounded deterministic version-comparison arguments."""

    work_id: str = Field(min_length=1, max_length=64)
    left_document_version_id: str = Field(min_length=1, max_length=64)
    right_document_version_id: str = Field(min_length=1, max_length=64)
    include_unchanged: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class ToolTraceRequest(StrictModel):
    """Bounded project relationship traversal arguments."""

    project_id: str = Field(min_length=1, max_length=64)
    work_id: str = Field(min_length=1, max_length=64)
    depth: int = Field(default=1, ge=1, le=3)
    node_limit: int = Field(default=50, ge=1, le=100)
    edge_limit: int = Field(default=100, ge=1, le=200)


class ToolExportProjectRequest(StrictModel):
    """Authorized governed-export arguments."""

    project_id: str = Field(min_length=1, max_length=64)
    table_id: str = Field(min_length=1, max_length=64)
    format: Literal["json", "csv", "markdown"] = "json"
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class AgentToolCallResource(StrictModel):
    """Structured result shared by every agent-tool transport."""

    schema_version: Literal["eeg.agent-tools.v1"]
    invocation_id: str
    tool_name: str
    result: dict[str, object]

    @classmethod
    def from_domain(cls, item: ToolCallResult) -> AgentToolCallResource:
        """Map one strict application result."""
        return cls.model_validate(to_json_value(item))


class AgentToolDefinitionResource(StrictModel):
    """Machine-readable registration contract for one bounded tool."""

    name: str
    description: str
    input_schema: dict[str, object]
