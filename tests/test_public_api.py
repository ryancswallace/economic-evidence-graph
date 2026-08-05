"""Tests for the intentional package API."""

import economic_evidence_graph


def test_public_exports_are_available() -> None:
    """Only the stable package entry points are exported."""
    expected_exports = {
        "EconomicEvidenceGraphError",
        "EegClient",
        "EegClientError",
        "Settings",
        "__version__",
        "create_app",
    }

    assert set(economic_evidence_graph.__all__) == expected_exports
    for name in economic_evidence_graph.__all__:
        assert hasattr(economic_evidence_graph, name)


def test_version_is_available() -> None:
    """A version string is always exposed."""
    assert economic_evidence_graph.__version__
