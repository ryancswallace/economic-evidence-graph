"""First-party Boston Fed and Board/FOMC connectors."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from defusedxml import ElementTree as SafeElementTree

from economic_evidence_graph.connectors.base import GovernedConnector, require_success
from economic_evidence_graph.domain import ContentLink, IdentifierValue
from economic_evidence_graph.exceptions import AcquisitionError
from economic_evidence_graph.ports import DiscoveryPage, DiscoveryRecord
from economic_evidence_graph.providers.http import GovernedHttpClient, GovernedResponse

_BOARD_ENDPOINTS = (
    ("https://www.federalreserve.gov/feeds/feds.xml", "rss"),
    ("https://www.federalreserve.gov/feeds/ifdp.xml", "rss"),
    ("https://www.federalreserve.gov/feeds/press_monetary.xml", "rss"),
    ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "html"),
    *(
        (f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm", "html")
        for year in range(2019, 1999, -1)
    ),
)

_WEB_DOCUMENT_SUFFIX = re.compile(r"\.(?:asp|aspx|cfm|htm|html|jsp|pdf|php)$", re.IGNORECASE)


class BoardFomcConnector(GovernedConnector):
    """Discover Board working papers and FOMC policy documents from first-party indexes."""

    source_code = "board_fomc"

    def __init__(
        self,
        http: GovernedHttpClient,
        *,
        endpoints: tuple[tuple[str, str], ...] = _BOARD_ENDPOINTS,
    ) -> None:
        super().__init__(http)
        self._endpoints = endpoints

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover one resumable index slice."""
        endpoint_index, offset = _cursor(cursor)
        if not 1 <= limit <= 100:
            raise ValueError("connector limit must be between 1 and 100")
        if endpoint_index >= len(self._endpoints):
            return DiscoveryPage((), None, None, ())
        endpoint, kind = self._endpoints[endpoint_index]
        response = self._http.get(
            source_code=self.source_code,
            action="discover_metadata",
            url=endpoint,
            content_class="first_party_index",
        )
        require_success(response.status_code, source_code=self.source_code)
        all_records = _rss_records(response) if kind == "rss" else _board_html_records(response)
        records = all_records[offset : offset + limit]
        next_cursor = _next_cursor(endpoint_index, offset + len(records), len(all_records), len(self._endpoints))
        return DiscoveryPage(tuple(records), next_cursor, str(len(all_records)), (response.trace,))


class BostonFedConnector(GovernedConnector):
    """Discover 2010-present Boston Fed research and policy pages from its sitemap."""

    source_code = "boston_fed"

    def __init__(self, http: GovernedHttpClient, *, sitemap_url: str = "https://www.bostonfed.org/sitemap.xml") -> None:
        super().__init__(http)
        self._sitemap_url = sitemap_url

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover a bounded slice of in-scope first-party publication URLs."""
        if not 1 <= limit <= 100:
            raise ValueError("connector limit must be between 1 and 100")
        try:
            offset = 0 if cursor is None else int(cursor)
        except ValueError as error:
            raise ValueError("Boston Fed cursor must be a nonnegative integer") from error
        if offset < 0:
            raise ValueError("Boston Fed cursor must be a nonnegative integer")
        response = self._http.get(
            source_code=self.source_code,
            action="discover_metadata",
            url=self._sitemap_url,
            content_class="first_party_sitemap",
        )
        require_success(response.status_code, source_code=self.source_code)
        urls = tuple(url for url in _sitemap_locations(response.content) if _in_boston_scope(url))
        selected = urls[offset : offset + limit]
        records = tuple(_boston_record(url, response.retrieved_at) for url in selected)
        next_offset = offset + len(records)
        return DiscoveryPage(
            records,
            str(next_offset) if next_offset < len(urls) else None,
            str(len(urls)),
            (response.trace,),
        )


def _rss_records(response: GovernedResponse) -> tuple[DiscoveryRecord, ...]:
    """Map RSS/Atom items using namespace-insensitive field names."""
    try:
        root = SafeElementTree.fromstring(response.content)
    except SafeElementTree.ParseError as error:
        raise AcquisitionError("Federal Reserve feed is malformed XML") from error
    items = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    records: list[DiscoveryRecord] = []
    for item in items:
        fields: dict[str, list[str]] = {}
        for child in item.iter():
            name = _local_name(child.tag)
            text = "".join(child.itertext()).strip()
            if text:
                fields.setdefault(name, []).append(text)
            href = child.attrib.get("href")
            if href:
                fields.setdefault("link", []).append(href)
        link = _first_field(fields, "link")
        title = _first_field(fields, "title")
        identifier = _first_field(fields, "guid") or _first_field(fields, "id") or link
        if link is None or title is None or identifier is None:
            continue
        raw = SafeElementTree.tostring(item, encoding="utf-8")
        published = _parse_rss_datetime(_first_field(fields, "pubDate") or _first_field(fields, "published"))
        records.append(
            DiscoveryRecord(
                source_code="board_fomc",
                source_identifier=identifier,
                metadata_url=link,
                title=title,
                authors=(*fields.get("creator", []), *fields.get("author", [])),
                abstract=_strip_html(_first_field(fields, "description") or _first_field(fields, "summary")),
                published_at=None if published is None else published.date(),
                asserted_updated_at=published,
                identifiers=(),
                content_links=(ContentLink(link, "text/html", "first_party_document"),),
                raw_metadata=raw,
                metadata_media_type="application/xml",
                discovered_at=response.retrieved_at,
            )
        )
    return tuple(records)


def _board_html_records(response: GovernedResponse) -> tuple[DiscoveryRecord, ...]:
    """Map document anchors from current or historical FOMC indexes."""
    parser = _FomcLinkParser(response.resolved_url)
    parser.feed(response.content.decode("utf-8", errors="replace"))
    records: list[DiscoveryRecord] = []
    for url, title in parser.records:
        published = _date_from_url_or_title(url, title)
        if published is not None and published.year < 2000:
            continue
        raw = f"{title}\n{url}".encode()
        records.append(
            DiscoveryRecord(
                source_code="board_fomc",
                source_identifier=url,
                metadata_url=response.resolved_url,
                title=title,
                authors=("Federal Open Market Committee",),
                abstract=None,
                published_at=published,
                asserted_updated_at=None,
                identifiers=(IdentifierValue("url_sha256", hashlib.sha256(url.encode()).hexdigest()),),
                content_links=(ContentLink(url, None, "fomc_policy_document"),),
                raw_metadata=raw,
                metadata_media_type="text/html",
                discovered_at=response.retrieved_at,
            )
        )
    return tuple(records)


def _sitemap_locations(content: bytes) -> tuple[str, ...]:
    """Read URL locations from a sitemap URL set."""
    try:
        root = SafeElementTree.fromstring(content)
    except SafeElementTree.ParseError as error:
        raise AcquisitionError("Boston Fed sitemap is malformed XML") from error
    return tuple(
        text
        for element in root.iter()
        if _local_name(element.tag) == "loc" and (text := (element.text or "").strip()).startswith("http")
    )


def _in_boston_scope(url: str) -> bool:
    """Apply the approved publication-family and date scope."""
    lower = url.lower()
    if "/publications/" not in lower:
        return False
    if not any(token in lower for token in ("research", "working-paper", "policy", "current-policy", "neppc")):
        return False
    years = re.findall(r"/(20\d{2})/", lower)
    return not years or int(years[0]) >= 2010


def _boston_record(url: str, observed_at: datetime) -> DiscoveryRecord:
    """Create a conservative metadata record from a first-party sitemap entry."""
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = _WEB_DOCUMENT_SUFFIX.sub("", slug)
    title = re.sub(r"[-_]", " ", slug).strip().title()
    year_match = re.search(r"/(20\d{2})/", url)
    published = date(int(year_match.group(1)), 1, 1) if year_match else None
    return DiscoveryRecord(
        source_code="boston_fed",
        source_identifier=url,
        metadata_url=url,
        title=title or "Boston Fed publication",
        authors=(),
        abstract=None,
        published_at=published,
        asserted_updated_at=None,
        identifiers=(IdentifierValue("url_sha256", hashlib.sha256(url.encode()).hexdigest()),),
        content_links=(ContentLink(url, "text/html", "first_party_document"),),
        raw_metadata=url.encode(),
        metadata_media_type="application/xml",
        discovered_at=observed_at,
    )


class _FomcLinkParser(HTMLParser):
    """Collect statement, minute, and projection document anchors."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._url: str | None = None
        self._text: list[str] = []
        self.records: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Begin a candidate anchor."""
        if tag.lower() == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            self._url = None if href is None else urljoin(self._base_url, href)
            self._text = []

    def handle_data(self, data: str) -> None:
        """Collect candidate anchor text."""
        if self._url is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Promote relevant first-party document anchors."""
        if tag.lower() != "a" or self._url is None:
            return
        title = " ".join("".join(self._text).split())
        lower = f"{self._url} {title}".lower()
        relevant = any(token in lower for token in ("statement", "minutes", "projection", "sep", "press release"))
        if relevant and self._url.startswith("https://www.federalreserve.gov/") and title:
            self.records.append((self._url, title))
        self._url = None


def _local_name(tag: str) -> str:
    """Remove an XML namespace from a tag."""
    return tag.rsplit("}", 1)[-1]


def _first_field(fields: dict[str, list[str]], key: str) -> str | None:
    """Return the first source field value."""
    values = fields.get(key, [])
    return values[0] if values else None


def _parse_rss_datetime(value: str | None) -> datetime | None:
    """Parse RFC 2822 or ISO feed dates to UTC."""
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value) if "," in value else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _strip_html(value: str | None) -> str | None:
    """Remove simple tags from feed descriptions."""
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    normalized = " ".join(text.split())
    return normalized or None


def _date_from_url_or_title(url: str, title: str) -> date | None:
    """Extract an explicitly represented policy date."""
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", f"{url} {title}")
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _cursor(value: str | None) -> tuple[int, int]:
    """Decode a feed-and-offset cursor."""
    if value is None:
        return 0, 0
    try:
        endpoint, offset = (int(part) for part in value.split(":", 1))
    except ValueError as error:
        raise ValueError("Board cursor must be endpoint:offset") from error
    if endpoint < 0 or offset < 0:
        raise ValueError("Board cursor values must not be negative")
    return endpoint, offset


def _next_cursor(endpoint: int, offset: int, count: int, endpoint_count: int) -> str | None:
    """Advance within an index or to the next configured index."""
    if offset < count:
        return f"{endpoint}:{offset}"
    return f"{endpoint + 1}:0" if endpoint + 1 < endpoint_count else None
