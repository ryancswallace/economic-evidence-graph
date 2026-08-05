"""Crossref REST metadata connector."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, cast

from economic_evidence_graph.connectors.base import GovernedConnector, require_success
from economic_evidence_graph.domain import ContentLink, IdentifierValue
from economic_evidence_graph.exceptions import AcquisitionError
from economic_evidence_graph.ports import DiscoveryPage, DiscoveryRecord
from economic_evidence_graph.providers.http import GovernedHttpClient


class CrossrefConnector(GovernedConnector):
    """Discover 2010-present candidate works through Crossref's polite pool."""

    source_code = "crossref"

    def __init__(self, http: GovernedHttpClient, *, crawler_email: str) -> None:
        super().__init__(http)
        self._email = crawler_email

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover one cursor page of macroeconomic and Federal Reserve records."""
        _validate_limit(limit)
        response = self._http.get(
            source_code=self.source_code,
            action="discover_metadata",
            url="https://api.crossref.org/works",
            content_class="bibliographic_metadata",
            params={
                "query.bibliographic": "macroeconomics monetary policy Federal Reserve",
                "filter": "from-pub-date:2010-01-01",
                "rows": limit,
                "cursor": cursor or "*",
                "mailto": self._email,
                "select": "DOI,title,author,abstract,published,created,URL,link,license,type,update-policy",
            },
        )
        require_success(response.status_code, source_code=self.source_code)
        try:
            document = cast(dict[str, Any], response.json())
            message = cast(dict[str, Any], document["message"])
            items = cast(list[dict[str, Any]], message["items"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AcquisitionError("Crossref response schema changed") from error
        records = tuple(_crossref_record(item, response.retrieved_at) for item in items)
        next_cursor = message.get("next-cursor")
        return DiscoveryPage(
            records=records,
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
            source_checkpoint=None,
            traces=(response.trace,),
        )


def _crossref_record(item: dict[str, Any], observed_at: datetime) -> DiscoveryRecord:
    """Map one Crossref item without silently reconciling fields."""
    doi = _optional_text(item.get("DOI"))
    source_identifier = doi or _required_text(item.get("URL"), "Crossref record identifier")
    titles = item.get("title")
    title = _required_text(titles[0] if isinstance(titles, list) and titles else None, "Crossref title")
    authors = tuple(
        " ".join(part for part in (_optional_text(author.get("given")), _optional_text(author.get("family"))) if part)
        for author in item.get("author", [])
        if isinstance(author, dict)
    )
    license_uri = None
    licenses = item.get("license")
    if isinstance(licenses, list) and licenses and isinstance(licenses[0], dict):
        license_uri = _optional_text(licenses[0].get("URL"))
    content_links = tuple(
        ContentLink(
            url=url,
            media_type=_optional_text(link.get("content-type")),
            content_class="linked_full_text",
            license_uri=license_uri,
        )
        for link in item.get("link", [])
        if isinstance(link, dict) and (url := _optional_text(link.get("URL"))) is not None
    )
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    identifiers = () if doi is None else (IdentifierValue("doi", doi.lower()),)
    return DiscoveryRecord(
        source_code="crossref",
        source_identifier=source_identifier,
        metadata_url=_optional_text(item.get("URL")) or f"https://doi.org/{doi}",
        title=title,
        authors=tuple(author for author in authors if author),
        abstract=_optional_text(item.get("abstract")),
        published_at=_crossref_date(item.get("published")),
        asserted_updated_at=_crossref_created(item.get("created")),
        identifiers=identifiers,
        content_links=content_links,
        raw_metadata=raw,
        metadata_media_type="application/json",
        discovered_at=observed_at,
    )


def _crossref_date(value: object) -> date | None:
    """Read a Crossref date-parts value."""
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    numbers = [int(item) for item in parts[0][:3]]
    return date(numbers[0], numbers[1] if len(numbers) > 1 else 1, numbers[2] if len(numbers) > 2 else 1)


def _crossref_created(value: object) -> datetime | None:
    """Read Crossref's created timestamp."""
    if not isinstance(value, dict):
        return None
    timestamp = value.get("date-time")
    if not isinstance(timestamp, str):
        return None
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)


def _optional_text(value: object) -> str | None:
    """Return stripped source text when present."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_text(value: object, field: str) -> str:
    """Require source text needed for a stable record."""
    text = _optional_text(value)
    if text is None:
        raise AcquisitionError(f"{field} is missing")
    return text


def _validate_limit(limit: int) -> None:
    """Require a small bounded discovery page."""
    if not 1 <= limit <= 100:
        raise ValueError("connector limit must be between 1 and 100")
