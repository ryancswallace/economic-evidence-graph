"""Public package interface for Economic Evidence Graph."""

from importlib.metadata import PackageNotFoundError, version

from economic_evidence_graph.api import create_app
from economic_evidence_graph.client import EegClient, EegClientError
from economic_evidence_graph.config import Settings
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

try:
    __version__ = version("economic-evidence-graph")
except PackageNotFoundError:  # pragma: no cover - source tree fallback before installation
    __version__ = "0+unknown"

__all__ = [
    "EconomicEvidenceGraphError",
    "EegClient",
    "EegClientError",
    "Settings",
    "__version__",
    "create_app",
]
