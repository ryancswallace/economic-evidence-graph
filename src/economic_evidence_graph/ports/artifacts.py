"""Artifact storage protocol."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Stable reference to exact content-addressed bytes."""

    uri: str
    sha256: str
    media_type: str
    byte_length: int


class ArtifactStore(Protocol):
    """Storage boundary for immutable raw, derived, and export artifacts."""

    def put_bytes(self, content: bytes, *, media_type: str) -> ArtifactRef:
        """Store exact bytes idempotently and return their stable reference."""
        ...

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        """Retrieve exact bytes and verify their content digest."""
        ...

    def delete(self, ref: ArtifactRef) -> bool:
        """Delete exact bytes when an explicit audited lifecycle action permits it."""
        ...

    def check(self) -> None:
        """Raise when the store is unavailable or not writable."""
        ...
