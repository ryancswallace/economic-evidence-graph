"""Persistence protocol for privacy-safe agent-tool audit records."""

from typing import Protocol

from economic_evidence_graph.domain.tools import ToolInvocationRecord


class ToolAuditRepository(Protocol):
    """Append-only persistence used by every agent-tool adapter."""

    def put_tool_invocation(self, item: ToolInvocationRecord) -> None:
        """Persist one terminal invocation without raw research content."""
        ...

    def list_tool_invocations(self, *, limit: int = 100) -> tuple[ToolInvocationRecord, ...]:
        """Return the newest bounded operational audit records."""
        ...
