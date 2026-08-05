"""Shared governed acquisition behavior and connector parsing helpers."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from economic_evidence_graph.domain import RequestTrace
from economic_evidence_graph.exceptions import AcquisitionError, EconomicEvidenceGraphError
from economic_evidence_graph.ports import (
    AcquisitionBatch,
    AcquisitionCandidate,
    AcquisitionFailureCandidate,
    DiscoveryRecord,
)
from economic_evidence_graph.providers.http import GovernedHttpClient


class GovernedConnector:
    """Base connector that retrieves every declared and discoverable full text."""

    source_code = "base"

    def __init__(self, http: GovernedHttpClient) -> None:
        self._http = http

    def acquire(self, record: DiscoveryRecord) -> AcquisitionBatch:
        """Fetch declared content and PDF links found on declared HTML pages."""
        pending = list(record.content_links)
        seen: set[str] = set()
        candidates: list[AcquisitionCandidate] = []
        failures: list[AcquisitionFailureCandidate] = []
        while pending:
            link = pending.pop(0)
            if link.url in seen:
                continue
            seen.add(link.url)
            try:
                response = self._http.get(
                    source_code=self.source_code,
                    action="fetch_content",
                    url=link.url,
                    content_class=link.content_class,
                )
            except EconomicEvidenceGraphError as error:
                failures.append(
                    AcquisitionFailureCandidate(
                        record=record,
                        requested_url=link.url,
                        failure_code=error.code,
                        detail=str(error),
                        occurred_at=error.occurred_at or record.discovered_at,
                        trace=error.governance_trace or RequestTrace((), ()),
                    )
                )
                continue
            if not 200 <= response.status_code < 300:
                failures.append(
                    AcquisitionFailureCandidate(
                        record=record,
                        requested_url=link.url,
                        failure_code=f"http_{response.status_code}",
                        detail="content request returned a non-success status",
                        occurred_at=response.retrieved_at,
                        trace=response.trace,
                    )
                )
                continue
            candidate = AcquisitionCandidate(
                record=record,
                requested_url=response.requested_url,
                resolved_url=response.resolved_url,
                media_type=response.media_type,
                content=response.content,
                retrieved_at=response.retrieved_at,
                http_status=response.status_code,
                license_uri=link.license_uri,
                trace=response.trace,
            )
            candidates.append(candidate)
            if response.media_type in {"text/html", "application/xhtml+xml"}:
                parser = _PdfLinkParser(response.resolved_url)
                parser.feed(response.content.decode("utf-8", errors="replace"))
                for url in parser.links[:5]:
                    pending.append(type(link)(url, "application/pdf", "linked_full_text_pdf", link.license_uri))
        return AcquisitionBatch(tuple(candidates), tuple(failures))


def require_success(status_code: int, *, source_code: str) -> None:
    """Reject non-success discovery responses with non-secret context."""
    if not 200 <= status_code < 300:
        raise AcquisitionError(
            "source discovery request returned a non-success status",
            context={"source_code": source_code, "status_code": str(status_code)},
        )


class _PdfLinkParser(HTMLParser):
    """Collect likely full-text PDF anchors from an HTML document."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect unique PDF links."""
        if tag.lower() != "a":
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
        if href is None:
            return
        absolute = urljoin(self._base_url, href)
        if ".pdf" in absolute.lower() and absolute not in self.links:
            self.links.append(absolute)
