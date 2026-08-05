"""Deterministic, inspectable local embeddings for the Step 3 pilot."""

from __future__ import annotations

import hashlib
import math
import re

from economic_evidence_graph.domain import TemporalSafetyClass
from economic_evidence_graph.serialization import record_sha256

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)?")
_SYNONYMS = {
    "centralbank": ("central", "bank", "monetary", "policy"),
    "cpi": ("consumer", "price", "inflation"),
    "employment": ("labor", "market", "jobs"),
    "fed": ("federal", "reserve", "central", "bank"),
    "fomc": ("federal", "open", "market", "committee", "monetary", "policy"),
    "gdp": ("gross", "domestic", "product", "output"),
    "inflation": ("prices", "cpi"),
    "jobless": ("unemployment", "labor", "market"),
    "qe": ("quantitative", "easing", "asset", "purchases"),
}


class DeterministicEconomicsEmbedding:
    """Hash economic terms into a normalized vector with no learned state.

    This T1 baseline makes local semantic-channel behavior reproducible and
    inspectable. It is deliberately behind the same provider boundary that a
    reviewed open-weight embedding model can implement later.
    """

    def __init__(self, dimension: int = 256) -> None:
        if dimension < 64 or dimension & (dimension - 1):
            raise ValueError("embedding dimension must be a power of two of at least 64")
        self._dimension = dimension
        self._config_id = f"eeg-embedding-t1-{record_sha256({'dimension': dimension, 'synonyms': _SYNONYMS})[:16]}"

    @property
    def config_id(self) -> str:
        """Return the immutable algorithm configuration identifier."""
        return self._config_id

    @property
    def dimension(self) -> int:
        """Return the fixed vector dimension."""
        return self._dimension

    @property
    def temporal_safety_class(self) -> TemporalSafetyClass:
        """Classify the deterministic transformation as historical-safe T1."""
        return TemporalSafetyClass.DETERMINISTIC

    def embed(self, text: str) -> tuple[float, ...]:
        """Return an L2-normalized feature-hashing vector."""
        tokens = _expanded_tokens(text)
        values = [0.0] * self._dimension
        for position, token in enumerate(tokens):
            _accumulate(values, token, 1.0)
            if position:
                _accumulate(values, f"{tokens[position - 1]}::{token}", 0.35)
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            values[0] = 1.0
            return tuple(values)
        return tuple(value / norm for value in values)


def _expanded_tokens(text: str) -> tuple[str, ...]:
    """Tokenize text and add a small, versioned economics vocabulary."""
    source = tuple(_TOKEN_PATTERN.findall(text.casefold()))
    expanded: list[str] = []
    for token in source:
        compact = token.replace("-", "")
        expanded.append(token)
        expanded.extend(_SYNONYMS.get(compact, ()))
    return tuple(expanded)


def _accumulate(values: list[float], feature: str, weight: float) -> None:
    """Add one stable signed feature to a hashing vector."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8, person=b"eeg-t1").digest()
    encoded = int.from_bytes(digest)
    index = encoded & (len(values) - 1)
    sign = -1.0 if encoded & (1 << 63) else 1.0
    values[index] += sign * weight
