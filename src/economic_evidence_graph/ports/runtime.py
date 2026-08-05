"""Small deterministic runtime protocols."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Time source used by application services."""

    def now(self) -> datetime:
        """Return an aware UTC timestamp."""
        ...


class IdFactory(Protocol):
    """Opaque identifier source for operational entities."""

    def new(self, prefix: str) -> str:
        """Return a new identifier carrying the supplied entity prefix."""
        ...
