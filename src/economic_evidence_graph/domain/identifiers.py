"""Stable identifier generation."""

import json
import re
from uuid import UUID, uuid5

from economic_evidence_graph.exceptions import EconomicEvidenceGraphError

_IDENTIFIER_NAMESPACE = UUID("bb625f70-f11a-5e24-a7a7-bd0a91e6f323")
_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,11}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def stable_id(prefix: str, *components: str) -> str:
    """Create a stable opaque identifier from canonical source components.

    Args:
        prefix: Short entity-kind prefix such as ``wrk`` or ``ver``.
        *components: Non-empty identity components whose order is significant.

    Returns:
        A namespaced UUIDv5 identifier with the supplied prefix.

    Raises:
        EconomicEvidenceGraphError: If the prefix or components are invalid.
    """
    if not _PREFIX_PATTERN.fullmatch(prefix):
        raise EconomicEvidenceGraphError("identifier prefix must be 2-12 lowercase alphanumeric characters")
    normalized = tuple(component.strip() for component in components)
    if not normalized or any(not component for component in normalized):
        raise EconomicEvidenceGraphError("stable identifiers require non-blank components")
    canonical_name = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}_{uuid5(_IDENTIFIER_NAMESPACE, canonical_name).hex}"


def content_id(prefix: str, sha256: str) -> str:
    """Create an identifier tied to an exact SHA-256 content digest.

    Args:
        prefix: Short entity-kind prefix.
        sha256: Lowercase hexadecimal SHA-256 digest.

    Returns:
        A stable identifier derived from the digest.

    Raises:
        EconomicEvidenceGraphError: If the digest is malformed.
    """
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise EconomicEvidenceGraphError("content identifiers require a lowercase SHA-256 digest")
    return stable_id(prefix, "sha256", sha256)
