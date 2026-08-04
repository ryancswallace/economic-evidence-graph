"""Public package interface for Economic Evidence Graph."""

from importlib.metadata import PackageNotFoundError, version

from economic_evidence_graph._core import greet
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

try:
    __version__ = version("economic-evidence-graph")
except PackageNotFoundError:  # pragma: no cover - source tree fallback before installation
    __version__ = "0+unknown"

__all__ = [
    "EconomicEvidenceGraphError",
    "__version__",
    "greet",
]
