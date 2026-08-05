"""Auditable contracts shared by agent-tool interfaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AgentToolName(StrEnum):
    """Stable names for the eight bounded research tools."""

    SEARCH_LITERATURE = "search_literature"
    GET_WORK = "get_work"
    RETRIEVE_EVIDENCE = "retrieve_evidence"
    COMPARE_EVIDENCE = "compare_evidence"
    COMPARE_VERSIONS = "compare_versions"
    TRACE_CITATIONS = "trace_citations"
    TRACE_POLICY_LINKS = "trace_policy_links"
    EXPORT_PROJECT = "export_project"


class ToolInvocationOutcome(StrEnum):
    """Privacy-safe terminal state for one tool invocation."""

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolInvocationRecord:
    """Append-only tool audit record that excludes raw queries and evidence text."""

    id: str
    actor_id: str
    tool_name: AgentToolName
    arguments_sha256: str
    outcome: ToolInvocationOutcome
    result_sha256: str | None
    target_ids: tuple[str, ...]
    duration_ms: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.actor_id.strip():
            raise EconomicEvidenceGraphError("tool invocation identity must not be blank")
        if not _SHA256_PATTERN.fullmatch(self.arguments_sha256):
            raise EconomicEvidenceGraphError("tool arguments digest must be SHA-256")
        if self.result_sha256 is not None and not _SHA256_PATTERN.fullmatch(self.result_sha256):
            raise EconomicEvidenceGraphError("tool result digest must be SHA-256")
        if self.duration_ms < 0:
            raise EconomicEvidenceGraphError("tool duration must not be negative")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(self.occurred_at):
            raise EconomicEvidenceGraphError("tool invocation timestamp must be timezone-aware UTC")
        if any(not item.strip() for item in self.target_ids):
            raise EconomicEvidenceGraphError("tool target identifiers must not be blank")
