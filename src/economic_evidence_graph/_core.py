"""Core package behavior."""

from economic_evidence_graph.exceptions import EconomicEvidenceGraphError


def greet(name: str = "world") -> str:
    """Return a friendly greeting.

    Args:
        name: Name to include in the greeting.

    Returns:
        A greeting for `name`.

    Raises:
        EconomicEvidenceGraphError: If `name` is blank.
    """
    cleaned = name.strip()
    if not cleaned:
        raise EconomicEvidenceGraphError("name must not be blank")
    return f"Hello, {cleaned}."
