"""Typed, bounded, auditable tools for research agents and thin clients."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import perf_counter_ns
from typing import cast

from economic_evidence_graph.application.projects import ProjectService
from economic_evidence_graph.application.retrieval import RetrievalService
from economic_evidence_graph.domain import (
    AgentToolName,
    EvidenceSnippet,
    PolicyLink,
    PolicyLinkType,
    ProjectExportFormat,
    SearchRequest,
    ToolInvocationOutcome,
    ToolInvocationRecord,
)
from economic_evidence_graph.exceptions import AuthorizationError, NotFoundError, PolicyDeniedError
from economic_evidence_graph.ports import Clock, IdFactory, ToolAuditRepository
from economic_evidence_graph.serialization import JsonValue, record_sha256, to_json_value

TOOL_SCHEMA_VERSION = "eeg.agent-tools.v1"
_TOOL_SEARCH_LIMIT = 20
_TOOL_VERSION_LIMIT = 100
_TOOL_NODE_LIMIT = 100
_TOOL_EDGE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """One structured tool result with an auditable invocation identifier."""

    schema_version: str
    invocation_id: str
    tool_name: AgentToolName
    result: dict[str, JsonValue]


class AgentToolService:
    """Expose exactly eight bounded tools over authorized application services."""

    def __init__(
        self,
        *,
        retrieval: RetrievalService,
        projects: ProjectService,
        audit: ToolAuditRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._retrieval = retrieval
        self._projects = projects
        self._audit = audit
        self._clock = clock
        self._ids = ids

    def search_literature(self, request: SearchRequest, *, actor_id: str) -> ToolCallResult:
        """Run provenance-complete retrieval under the agent result cap."""
        if request.limit > _TOOL_SEARCH_LIMIT:
            raise ValueError(f"agent search limit must not exceed {_TOOL_SEARCH_LIMIT}")
        arguments = _object(request)
        return self._invoke(
            AgentToolName.SEARCH_LITERATURE,
            actor_id,
            arguments,
            lambda: _object(self._retrieval.search(request)),
        )

    def get_work(self, work_id: str, *, actor_id: str) -> ToolCallResult:
        """Return canonical work metadata and bounded version projections."""
        arguments: dict[str, JsonValue] = {"work_id": work_id}

        def execute() -> dict[str, JsonValue]:
            versions = self._retrieval.work_versions(work_id)
            if not versions:
                raise NotFoundError("work was not found in the active search generation", context={"work_id": work_id})
            generation = self._retrieval.active_generation()
            representative = versions[0]
            return {
                "work_id": work_id,
                "title": _text(representative, "title"),
                "authors": _strings(representative.get("authors")),
                "versions": [_work_version(item) for item in versions],
                "index_generation_id": generation.id,
                "corpus_snapshot_id": generation.corpus_snapshot_id,
                "truncated": len(versions) == 50,
            }

        return self._invoke(AgentToolName.GET_WORK, actor_id, arguments, execute)

    def retrieve_evidence(self, evidence_unit_id: str, *, actor_id: str) -> ToolCallResult:
        """Return one verbatim, policy-gated excerpt without generation."""
        arguments: dict[str, JsonValue] = {"evidence_unit_id": evidence_unit_id}

        def execute() -> dict[str, JsonValue]:
            generation = self._retrieval.active_generation()
            return {
                "evidence": _object(self._retrieval.evidence(evidence_unit_id)),
                "index_generation_id": generation.id,
                "corpus_snapshot_id": generation.corpus_snapshot_id,
            }

        return self._invoke(AgentToolName.RETRIEVE_EVIDENCE, actor_id, arguments, execute)

    def compare_evidence(self, project_id: str, table_id: str, *, actor_id: str) -> ToolCallResult:
        """Return an authorized, current-policy comparison table."""
        arguments: dict[str, JsonValue] = {"project_id": project_id, "table_id": table_id}
        return self._invoke(
            AgentToolName.COMPARE_EVIDENCE,
            actor_id,
            arguments,
            lambda: self._projects.compare_table(project_id, table_id, actor_id=actor_id),
        )

    def compare_versions(
        self,
        work_id: str,
        left_document_version_id: str,
        right_document_version_id: str,
        *,
        actor_id: str,
        include_unchanged: bool = False,
        limit: int = 50,
    ) -> ToolCallResult:
        """Compare policy-gated sections without labeling changes substantive."""
        if not 1 <= limit <= _TOOL_VERSION_LIMIT:
            raise ValueError(f"version comparison limit must be between 1 and {_TOOL_VERSION_LIMIT}")
        arguments: dict[str, JsonValue] = {
            "work_id": work_id,
            "left_document_version_id": left_document_version_id,
            "right_document_version_id": right_document_version_id,
            "include_unchanged": include_unchanged,
            "limit": limit,
        }

        def execute() -> dict[str, JsonValue]:
            versions = self._retrieval.work_versions(work_id)
            version_ids = {_text(item, "document_version_id") for item in versions}
            requested = {left_document_version_id, right_document_version_id}
            if not requested.issubset(version_ids):
                raise NotFoundError(
                    "both versions must belong to the requested work in the active generation",
                    context={"work_id": work_id},
                )
            left = self._retrieval.version_evidence(left_document_version_id, limit=_TOOL_VERSION_LIMIT)
            right = self._retrieval.version_evidence(right_document_version_id, limit=_TOOL_VERSION_LIMIT)
            changes = _compare_snippets(left, right, include_unchanged=include_unchanged)
            selected = changes[:limit]
            generation = self._retrieval.active_generation()
            return {
                "work_id": work_id,
                "left_document_version_id": left_document_version_id,
                "right_document_version_id": right_document_version_id,
                "changes": selected,
                "change_count": len(changes),
                "truncated": len(changes) > len(selected),
                "index_generation_id": generation.id,
                "corpus_snapshot_id": generation.corpus_snapshot_id,
                "warnings": [
                    "sections are aligned deterministically by section path and locator",
                    "a reported change is textual and is not labeled substantive",
                ],
            }

        return self._invoke(AgentToolName.COMPARE_VERSIONS, actor_id, arguments, execute)

    def trace_citations(
        self,
        project_id: str,
        work_id: str,
        *,
        actor_id: str,
        depth: int = 1,
        node_limit: int = 50,
        edge_limit: int = 100,
    ) -> ToolCallResult:
        """Traverse only direct-citation project edges under explicit caps."""
        return self._trace(
            AgentToolName.TRACE_CITATIONS,
            project_id,
            work_id,
            actor_id=actor_id,
            depth=depth,
            node_limit=node_limit,
            edge_limit=edge_limit,
            relationship_types=frozenset({PolicyLinkType.DIRECT_CITATION}),
        )

    def trace_policy_links(
        self,
        project_id: str,
        work_id: str,
        *,
        actor_id: str,
        depth: int = 1,
        node_limit: int = 50,
        edge_limit: int = 100,
    ) -> ToolCallResult:
        """Traverse typed project policy links without collapsing semantics."""
        return self._trace(
            AgentToolName.TRACE_POLICY_LINKS,
            project_id,
            work_id,
            actor_id=actor_id,
            depth=depth,
            node_limit=node_limit,
            edge_limit=edge_limit,
            relationship_types=frozenset(PolicyLinkType),
        )

    def export_project(
        self,
        project_id: str,
        table_id: str,
        *,
        actor_id: str,
        format: ProjectExportFormat,
        expected_revision: int,
        idempotency_key: str,
    ) -> ToolCallResult:
        """Create one governed project export and return bounded artifact metadata."""
        arguments: dict[str, JsonValue] = {
            "project_id": project_id,
            "table_id": table_id,
            "format": format.value,
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
        }
        return self._invoke(
            AgentToolName.EXPORT_PROJECT,
            actor_id,
            arguments,
            lambda: _object(
                self._projects.export_table(
                    project_id,
                    table_id,
                    actor_id=actor_id,
                    format=format,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                )
            ),
        )

    def _trace(
        self,
        tool_name: AgentToolName,
        project_id: str,
        work_id: str,
        *,
        actor_id: str,
        depth: int,
        node_limit: int,
        edge_limit: int,
        relationship_types: frozenset[PolicyLinkType],
    ) -> ToolCallResult:
        """Apply shared authorization, traversal, and audit controls."""
        if not 1 <= depth <= 3:
            raise ValueError("relationship depth must be between 1 and 3")
        if not 1 <= node_limit <= _TOOL_NODE_LIMIT or not 1 <= edge_limit <= _TOOL_EDGE_LIMIT:
            raise ValueError("relationship node or edge limit exceeds the agent-tool bound")
        arguments: dict[str, JsonValue] = {
            "project_id": project_id,
            "work_id": work_id,
            "depth": depth,
            "node_limit": node_limit,
            "edge_limit": edge_limit,
        }

        def execute() -> dict[str, JsonValue]:
            bundle = self._projects.get(project_id, actor_id=actor_id)
            links = tuple(item for item in bundle.policy_links if item.relationship_type in relationship_types)
            nodes, edges, truncated = _bounded_relationships(
                links,
                start=work_id,
                depth=depth,
                node_limit=node_limit,
                edge_limit=edge_limit,
            )
            return {
                "project_id": project_id,
                "start_work_id": work_id,
                "nodes": nodes,
                "edges": edges,
                "truncated": truncated,
                "warnings": ["typed adjacency does not establish causal influence"],
            }

        return self._invoke(tool_name, actor_id, arguments, execute)

    def _invoke(
        self,
        tool_name: AgentToolName,
        actor_id: str,
        arguments: dict[str, JsonValue],
        execute: Callable[[], dict[str, JsonValue]],
    ) -> ToolCallResult:
        """Execute one tool and fail closed if its append-only audit cannot persist."""
        if not actor_id.strip() or len(actor_id) > 255:
            raise AuthorizationError("agent tool actor identity is invalid")
        invocation_id = self._ids.new("toolrun")
        started = perf_counter_ns()
        result: dict[str, JsonValue] | None = None
        outcome = ToolInvocationOutcome.ERROR
        try:
            result = execute()
            outcome = ToolInvocationOutcome.SUCCESS
        except (AuthorizationError, PolicyDeniedError):
            outcome = ToolInvocationOutcome.DENIED
            raise
        finally:
            duration_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
            self._audit.put_tool_invocation(
                ToolInvocationRecord(
                    id=invocation_id,
                    actor_id=actor_id,
                    tool_name=tool_name,
                    arguments_sha256=record_sha256(arguments),
                    outcome=outcome,
                    result_sha256=None if result is None else record_sha256(result),
                    target_ids=_target_ids(arguments),
                    duration_ms=duration_ms,
                    occurred_at=self._clock.now(),
                )
            )
        if result is None:  # pragma: no cover - exceptions leave before this guard
            raise RuntimeError("tool invocation completed without a result")
        return ToolCallResult(TOOL_SCHEMA_VERSION, invocation_id, tool_name, result)


def _object(value: object) -> dict[str, JsonValue]:
    """Serialize one strict domain object for a structured tool result."""
    payload = to_json_value(value)
    if not isinstance(payload, dict):
        raise TypeError("tool result must be a JSON object")
    return payload


def _work_version(item: Mapping[str, object]) -> dict[str, JsonValue]:
    """Remove embeddings and internal provider fields from a work projection."""
    fields = (
        "document_version_id",
        "source_record_id",
        "source_code",
        "source_identifier",
        "source_url",
        "published_at",
        "observed_at",
        "eligible_from",
        "date_basis",
        "date_evidence",
        "date_uncertainty",
        "temporal_safety_class",
        "policy_version",
        "rights_statuses",
        "quality_warnings",
    )
    return {field: to_json_value(item.get(field)) for field in fields}


def _compare_snippets(
    left: tuple[EvidenceSnippet, ...],
    right: tuple[EvidenceSnippet, ...],
    *,
    include_unchanged: bool,
) -> list[JsonValue]:
    """Align bounded excerpts by structural locator and report textual change only."""
    left_items = {_snippet_key(item): item for item in left}
    right_items = {_snippet_key(item): item for item in right}
    changes: list[JsonValue] = []
    for key in sorted(left_items.keys() | right_items.keys()):
        left_item = left_items.get(key)
        right_item = right_items.get(key)
        if left_item is None:
            kind = "added"
        elif right_item is None:
            kind = "removed"
        elif left_item.text_sha256 == right_item.text_sha256:
            kind = "unchanged"
        else:
            kind = "changed"
        if kind == "unchanged" and not include_unchanged:
            continue
        item = left_item or right_item
        if item is None:  # pragma: no cover - union keys always have one side
            continue
        changes.append(
            {
                "kind": kind,
                "section_path": list(item.section_path),
                "locator": item.locator,
                "left": None if left_item is None else _object(left_item),
                "right": None if right_item is None else _object(right_item),
            }
        )
    return changes


def _snippet_key(item: EvidenceSnippet) -> str:
    """Build a deterministic structural comparison key."""
    return f"{' / '.join(item.section_path)}\u0000{item.locator}"


def _bounded_relationships(
    links: tuple[PolicyLink, ...],
    *,
    start: str,
    depth: int,
    node_limit: int,
    edge_limit: int,
) -> tuple[list[JsonValue], list[JsonValue], bool]:
    """Traverse project edges breadth-first under hard node and edge caps."""
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    nodes = {start}
    edge_ids: set[str] = set()
    edges: list[JsonValue] = []
    truncated = False
    ordered_links = sorted(links, key=lambda item: item.id)
    while queue:
        current, level = queue.popleft()
        if level >= depth:
            continue
        for link in ordered_links:
            if current not in {link.research_work_id, link.policy_work_id} or link.id in edge_ids:
                continue
            other = link.policy_work_id if current == link.research_work_id else link.research_work_id
            if len(edges) >= edge_limit or (other not in nodes and len(nodes) >= node_limit):
                truncated = True
                continue
            edge_ids.add(link.id)
            edges.append(to_json_value(link))
            if other not in nodes:
                nodes.add(other)
                queue.append((other, level + 1))
    return [to_json_value(item) for item in sorted(nodes)], edges, truncated


def _target_ids(arguments: Mapping[str, JsonValue]) -> tuple[str, ...]:
    """Retain only opaque identifiers, never raw query or evidence content, in audit."""
    allowed = {
        "document_version_id",
        "evidence_unit_id",
        "left_document_version_id",
        "project_id",
        "right_document_version_id",
        "table_id",
        "work_id",
    }
    return tuple(sorted(str(value) for key, value in arguments.items() if key in allowed and isinstance(value, str)))


def _text(item: Mapping[str, object], key: str) -> str:
    """Read one required text projection field."""
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"search projection field is invalid: {key}")
    return value


def _strings(value: object) -> list[JsonValue]:
    """Read one string-list projection field."""
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise TypeError("search projection string list is invalid")
    return [cast(str, item) for item in value]
