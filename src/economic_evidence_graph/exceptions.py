"""Package-specific exceptions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from economic_evidence_graph.domain import RequestTrace


class EconomicEvidenceGraphError(ValueError):
    """Base class for expected application failures.

    Args:
        message: Human-readable failure description.
        code: Stable machine-readable error code.
        context: Strict-JSON-safe diagnostic context.
        governance_trace: Optional decisions observed before a governed failure.
        occurred_at: Optional timestamp supplied by the failing boundary.
    """

    default_code = "economic_evidence_graph_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: Mapping[str, str] | None = None,
        governance_trace: RequestTrace | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.context = MappingProxyType(dict(context or {}))
        self.governance_trace = governance_trace
        self.occurred_at = occurred_at


class ConfigurationError(EconomicEvidenceGraphError):
    """Raised when runtime configuration is absent or invalid."""

    default_code = "configuration_error"


class ConflictError(EconomicEvidenceGraphError):
    """Raised when an immutable record conflicts with stored state."""

    default_code = "immutable_record_conflict"


class NotFoundError(EconomicEvidenceGraphError):
    """Raised when a requested application resource does not exist."""

    default_code = "resource_not_found"


class AuthorizationError(EconomicEvidenceGraphError):
    """Raised when an actor lacks permission for a project operation."""

    default_code = "project_authorization_denied"


class InvalidStateTransitionError(EconomicEvidenceGraphError):
    """Raised when an aggregate cannot enter the requested state."""

    default_code = "invalid_state_transition"


class PolicyDeniedError(EconomicEvidenceGraphError):
    """Raised when source policy or robots rules deny an operation."""

    default_code = "source_policy_denied"


class AcquisitionError(EconomicEvidenceGraphError):
    """Raised when a governed network acquisition cannot complete safely."""

    default_code = "acquisition_error"


class DocumentParseError(EconomicEvidenceGraphError):
    """Raised with a stable quarantine reason for rejected document bytes."""

    default_code = "document_parse_error"


class CapacityExceededError(EconomicEvidenceGraphError):
    """Raised when a configured local pilot storage or record ceiling is reached."""

    default_code = "pilot_capacity_exceeded"


class SearchBackendError(EconomicEvidenceGraphError):
    """Raised when an index operation fails or returns an invalid response."""

    default_code = "search_backend_error"
