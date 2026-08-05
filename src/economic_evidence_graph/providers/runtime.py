"""Local clock and operational identifier providers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from economic_evidence_graph.domain import stable_id


class SystemClock:
    """Production wall clock returning aware UTC timestamps."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """Deterministic clock for fixtures and tests."""

    value: datetime

    def now(self) -> datetime:
        """Return the configured timestamp."""
        return self.value


class RandomIdFactory:
    """UUID-based operational identifier provider."""

    def new(self, prefix: str) -> str:
        """Return a fresh opaque identifier."""
        return f"{prefix}_{uuid4().hex}"


@dataclass(slots=True)
class DeterministicIdFactory:
    """Sequence-based stable identifier provider for tests."""

    seed: str
    _counter: int = 0
    _lock: Lock = field(default_factory=Lock)

    def new(self, prefix: str) -> str:
        """Return the next stable identifier in the seeded sequence."""
        with self._lock:
            self._counter += 1
            return stable_id(prefix, self.seed, str(self._counter))
