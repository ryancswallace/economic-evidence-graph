"""CORE API v3 metadata and full-text connector."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, cast

from economic_evidence_graph.connectors.base import GovernedConnector, require_success
from economic_evidence_graph.domain import ContentLink, IdentifierValue
from economic_evidence_graph.exceptions import AcquisitionError
from economic_evidence_graph.ports import DiscoveryPage, DiscoveryRecord
from economic_evidence_graph.providers.http import GovernedHttpClient


class CoreConnector(GovernedConnector):
    """Discover candidate works using an API key kept outside persisted state."""

    source_code = "core"

    def __init__(self, http: GovernedHttpClient, *, api_key: str) -> None:
        super().__init__(http)
        if not api_key:
            raise ValueError("CORE API key is required")
        self._authorization = f"Bearer {api_key}"

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover a bounded offset page of 2010-present CORE works."""
        if not 1 <= limit <= 100:
            raise ValueError("connector limit must be between 1 and 100")
        try:
            offset = 0 if cursor is None else int(cursor)
        except ValueError as error:
            raise ValueError("CORE cursor must be a nonnegative integer") from error
        if offset < 0:
            raise ValueError("CORE cursor must be a nonnegative integer")
        response = self._http.get(
            source_code=self.source_code,
            action="discover_metadata",
            url="https://api.core.ac.uk/v3/search/works",
            content_class="bibliographic_metadata",
            headers={"Authorization": self._authorization},
            params={
                "q": '(macroeconomics OR "monetary policy" OR "Federal Reserve") AND yearPublished>=2010',
                "limit": limit,
                "offset": offset,
            },
        )
        require_success(response.status_code, source_code=self.source_code)
        try:
            document = cast(dict[str, Any], response.json())
            results = cast(list[dict[str, Any]], document["results"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AcquisitionError("CORE response schema changed") from error
        records = tuple(_core_record(item, response.retrieved_at) for item in results)
        total = document.get("totalHits")
        next_offset = offset + len(results)
        next_cursor = None if not results or (isinstance(total, int) and next_offset >= total) else str(next_offset)
        return DiscoveryPage(records, next_cursor, str(total) if isinstance(total, int) else None, (response.trace,))


def _core_record(item: dict[str, Any], observed_at: datetime) -> DiscoveryRecord:
    """Map one CORE work while preserving its raw observed metadata."""
    identifier = str(item.get("id", "")).strip()
    title = str(item.get("title", "")).strip()
    if not identifier or not title:
        raise AcquisitionError("CORE record omitted id or title")
    doi = _text(item.get("doi"))
    authors = tuple(_core_author(author) for author in item.get("authors", []) if _core_author(author))
    links: list[ContentLink] = []
    for key in ("downloadUrl", "fullTextLink"):
        if (url := _text(item.get(key))) is not None:
            links.append(ContentLink(url, None, "linked_full_text", _text(item.get("license"))))
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    metadata_url = _text(item.get("sourceFulltextUrls"))
    return DiscoveryRecord(
        source_code="core",
        source_identifier=identifier,
        metadata_url=metadata_url or f"https://core.ac.uk/works/{identifier}",
        title=title,
        authors=authors,
        abstract=_text(item.get("abstract")),
        published_at=_core_date(item.get("publishedDate") or item.get("yearPublished")),
        asserted_updated_at=_core_datetime(item.get("updatedDate")),
        identifiers=() if doi is None else (IdentifierValue("doi", doi.lower()),),
        content_links=tuple(dict.fromkeys(links)),
        raw_metadata=raw,
        metadata_media_type="application/json",
        discovered_at=observed_at,
    )


def _core_author(value: object) -> str:
    """Normalize CORE's string or object author shape."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    return ""


def _core_date(value: object) -> date | None:
    """Read a year or ISO date without inventing precision beyond January 1."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]) if len(text) >= 10 else date(int(text[:4]), 1, 1)
    except ValueError:
        return None


def _core_datetime(value: object) -> datetime | None:
    """Read an optional CORE update timestamp."""
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    """Return a usable scalar string."""
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value is not None and str(value).strip() else None
