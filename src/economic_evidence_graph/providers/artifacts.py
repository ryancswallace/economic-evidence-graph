"""Content-addressed local artifact providers."""

import hashlib
import os
import tempfile
from pathlib import Path
from threading import RLock

from economic_evidence_graph.exceptions import ConflictError, NotFoundError
from economic_evidence_graph.ports import ArtifactRef

_URI_PREFIX = "artifact://sha256/"


def _reference(content: bytes, media_type: str) -> ArtifactRef:
    """Build a stable artifact reference."""
    digest = hashlib.sha256(content).hexdigest()
    return ArtifactRef(
        uri=f"{_URI_PREFIX}{digest}",
        sha256=digest,
        media_type=media_type,
        byte_length=len(content),
    )


def _verify(content: bytes, ref: ArtifactRef) -> None:
    """Verify exact artifact identity and length."""
    digest = hashlib.sha256(content).hexdigest()
    if digest != ref.sha256 or len(content) != ref.byte_length:
        raise ConflictError("artifact bytes do not match their content-addressed reference")


class InMemoryArtifactStore:
    """Thread-safe content-addressed store for tests."""

    def __init__(self) -> None:
        self._content: dict[str, bytes] = {}
        self._lock = RLock()

    def put_bytes(self, content: bytes, *, media_type: str) -> ArtifactRef:
        """Store exact bytes idempotently."""
        ref = _reference(content, media_type)
        with self._lock:
            prior = self._content.get(ref.sha256)
            if prior is not None and prior != content:
                raise ConflictError("SHA-256 collision detected in artifact store")
            self._content[ref.sha256] = bytes(content)
        return ref

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        """Read and verify exact artifact bytes."""
        with self._lock:
            try:
                content = self._content[ref.sha256]
            except KeyError as error:
                raise NotFoundError("artifact was not found", context={"sha256": ref.sha256}) from error
        _verify(content, ref)
        return content

    def delete(self, ref: ArtifactRef) -> bool:
        """Delete an exact in-memory artifact if present."""
        with self._lock:
            return self._content.pop(ref.sha256, None) is not None

    def check(self) -> None:
        """Confirm the in-memory provider is usable."""


class FileArtifactStore:
    """Atomic content-addressed storage rooted on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    def put_bytes(self, content: bytes, *, media_type: str) -> ArtifactRef:
        """Store bytes atomically under their SHA-256 digest."""
        ref = _reference(content, media_type)
        target = self._path(ref.sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            prior = target.read_bytes()
            _verify(prior, ref)
            return ref

        file_descriptor, temporary_name = tempfile.mkstemp(prefix="artifact-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                _verify(target.read_bytes(), ref)
            temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
        return ref

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        """Read and verify exact artifact bytes."""
        target = self._path(ref.sha256)
        try:
            content = target.read_bytes()
        except FileNotFoundError as error:
            raise NotFoundError("artifact was not found", context={"sha256": ref.sha256}) from error
        _verify(content, ref)
        return content

    def delete(self, ref: ArtifactRef) -> bool:
        """Delete an exact filesystem artifact if present."""
        target = self._path(ref.sha256)
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True

    def check(self) -> None:
        """Confirm the artifact root can be created and written."""
        self._root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self._root):
            pass

    def _path(self, sha256: str) -> Path:
        """Resolve a digest without accepting caller-controlled paths."""
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise NotFoundError("artifact digest is malformed")
        return self._root / sha256[:2] / sha256[2:4] / sha256
