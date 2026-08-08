"""Recorded-shape contract tests for all five source connectors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from economic_evidence_graph.connectors import (
    BoardFomcConnector,
    BostonFedConnector,
    CoreConnector,
    CrossrefConnector,
    RepecConnector,
    RepecRsyncConnector,
    parse_redif,
)
from economic_evidence_graph.connectors.core import _core_record
from economic_evidence_graph.connectors.crossref import _crossref_record
from economic_evidence_graph.connectors.federal_reserve import (
    _date_from_url_or_title,
    _parse_rss_datetime,
    _sitemap_locations,
)
from economic_evidence_graph.connectors.repec import _handle_path, _rsync_executable, _run_rsync, redif_as_json
from economic_evidence_graph.exceptions import AcquisitionError
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.providers import DeterministicIdFactory, FrozenClock, GovernedHttpClient

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)


def _client(handler: object) -> GovernedHttpClient:
    """Create a deterministic connector transport with permissive robots fixtures."""
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")

    def governed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        return handler(request)  # type: ignore[operator]

    return GovernedHttpClient(
        client=httpx.Client(transport=httpx.MockTransport(governed)),
        registry=registry,
        policy=PolicyEngine(registry),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("connectors"),
        crawler_email="crawler@example.org",
        resolver=lambda _host: ("93.184.216.34",),
        sleeper=lambda _seconds: None,
        monotonic=lambda: 1.0,
    )


def test_crossref_discovery_and_recursive_full_text_acquisition() -> None:
    """Crossref maps identifiers and retrieves linked HTML plus its PDF."""
    item = {
        "DOI": "10.1234/TEST",
        "title": ["Monetary Policy Transmission"],
        "author": [{"given": "Ada", "family": "Economist"}],
        "abstract": "<jats:p>Evidence</jats:p>",
        "published": {"date-parts": [[2024, 3]]},
        "created": {"date-time": "2024-03-02T01:00:00Z"},
        "URL": "https://doi.org/10.1234/TEST",
        "link": [{"URL": "https://publisher.example/paper", "content-type": "text/html"}],
        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": [item], "next-cursor": "next"}}, request=request)
        if request.url.path == "/paper":
            return httpx.Response(
                200,
                text='<html><body><a href="/paper.pdf">PDF</a></body></html>',
                headers={"content-type": "text/html"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"%PDF-fixture",
            headers={"content-type": "application/pdf"},
            request=request,
        )

    connector = CrossrefConnector(_client(handler), crawler_email="crawler@example.org")
    page = connector.discover(cursor=None, limit=10)
    assert page.next_cursor == "next"
    assert page.records[0].identifiers[0].value == "10.1234/test"
    assert page.records[0].published_at is not None
    assert page.records[0].published_at.month == 3
    batch = connector.acquire(page.records[0])
    assert [asset.media_type for asset in batch.candidates] == ["text/html", "application/pdf"]
    assert all(asset.license_uri for asset in batch.candidates)
    assert batch.failures == ()


def test_crossref_schema_and_limit_failures() -> None:
    """Crossref rejects unbounded pages and upstream schema drift."""
    with pytest.raises(ValueError, match="limit"):
        CrossrefConnector(_client(lambda request: httpx.Response(200, request=request)), crawler_email="x@y").discover(
            cursor=None, limit=0
        )
    connector = CrossrefConnector(
        _client(lambda request: httpx.Response(200, json={"wrong": {}}, request=request)),
        crawler_email="x@y",
    )
    with pytest.raises(AcquisitionError, match="schema"):
        connector.discover(cursor=None, limit=1)


def test_connector_retains_http_and_network_acquisition_failures() -> None:
    """Representation failures remain bounded evidence instead of aborting metadata."""
    record = _crossref_record(
        {
            "DOI": "10.1234/failures",
            "URL": "https://doi.org/10.1234/failures",
            "title": ["Failure evidence"],
            "link": [
                {"URL": "https://publisher.example/forbidden", "content-type": "text/html"},
                {"URL": "http://127.0.0.1/private", "content-type": "text/html"},
            ],
        },
        _NOW,
    )
    connector = CrossrefConnector(
        _client(lambda request: httpx.Response(403, request=request)),
        crawler_email="crawler@example.org",
    )
    batch = connector.acquire(record)
    assert batch.candidates == ()
    assert [failure.failure_code for failure in batch.failures] == ["http_403", "acquisition_error"]


def test_core_discovery_maps_full_text_and_cursor() -> None:
    """CORE keeps its key in a header and maps available download URLs."""
    seen_authorization: list[str] = []
    item = {
        "id": 42,
        "title": "Macroeconomic Evidence",
        "authors": [{"name": "A. Author"}, "B. Author"],
        "abstract": "Abstract",
        "doi": "10.9999/core",
        "publishedDate": "2022-04-03",
        "updatedDate": "2023-01-01T00:00:00Z",
        "downloadUrl": "https://core.ac.uk/download/42.pdf",
        "license": "unknown",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/search/works":
            seen_authorization.append(request.headers["Authorization"])
            return httpx.Response(200, json={"results": [item], "totalHits": 2}, request=request)
        return httpx.Response(200, content=b"%PDF-core", headers={"content-type": "application/pdf"}, request=request)

    connector = CoreConnector(_client(handler), api_key="not-persisted")
    page = connector.discover(cursor=None, limit=1)
    assert page.next_cursor == "1"
    assert page.records[0].authors == ("A. Author", "B. Author")
    assert page.records[0].identifiers[0].scheme == "doi"
    assert seen_authorization == ["Bearer not-persisted"]
    assert connector.acquire(page.records[0]).candidates[0].content == b"%PDF-core"


@pytest.mark.parametrize("cursor", ["bad", "-1"])
def test_core_rejects_bad_cursor_and_schema(cursor: str) -> None:
    """CORE pagination remains bounded and resumable."""
    connector = CoreConnector(
        _client(lambda request: httpx.Response(200, json={"results": []}, request=request)), api_key="x"
    )
    with pytest.raises(ValueError, match="cursor"):
        connector.discover(cursor=cursor, limit=1)
    with pytest.raises(ValueError, match="key"):
        CoreConnector(_client(lambda request: httpx.Response(200, request=request)), api_key="")
    broken = CoreConnector(_client(lambda request: httpx.Response(200, json={}, request=request)), api_key="x")
    with pytest.raises(AcquisitionError, match="schema"):
        broken.discover(cursor=None, limit=1)


def test_redif_parser_and_repec_connector_preserve_repeated_fields() -> None:
    """ReDIF continuations, legacy encoding, identifiers, and files remain explicit."""
    redif = (
        b"Template-Type: ReDIF-Paper 1.0\n"
        b"Title: Monetary Policy\n"
        b" continuation evidence\n"
        b"Author-Name: Ada One\nAuthor-Name: Bob Two\n"
        b"Handle: RePEc:fip:fedbwp:24-1\n"
        b"Creation-Date: 2024-03\nDOI: 10.1/ABC\n"
        b"File-URL: https://www.bostonfed.org/paper.pdf\nFile-Format: application/pdf\n\n"
        b"Template-Type: ReDIF-Paper 1.0\nTitle: Second\nAuthor-Name: C\n"
        b"Handle: RePEc:fip:fedbwp:24-2\nCreation-Date: 2024\n"
    )
    assert parse_redif(redif)[0]["author-name"] == ("Ada One", "Bob Two")
    assert parse_redif("Template-Type: ReDIF-Paper 1.0\nTitle: Caf\xe9\n".encode("cp1252"))[0]["title"] == ("Café",)

    connector = RepecConnector(
        _client(
            lambda request: httpx.Response(
                200,
                content=redif,
                headers={"content-type": "text/plain"},
                request=request,
            )
        ),
        endpoints=("https://ideas.repec.org/RePEc/fip/test.rdf",),
    )
    first = connector.discover(cursor=None, limit=1)
    assert first.next_cursor == "0:1"
    assert first.records[0].title == "Monetary Policy continuation evidence"
    assert first.records[0].identifiers[-1].value == "10.1/abc"
    second = connector.discover(cursor=first.next_cursor, limit=10)
    assert second.next_cursor is None
    assert second.records[0].source_identifier.endswith("24-2")
    assert connector.discover(cursor="1:0", limit=1).records == ()
    with pytest.raises(ValueError, match="cursor"):
        connector.discover(cursor="bad", limit=1)


def test_board_rss_and_fomc_html_discovery() -> None:
    """Board feeds and FOMC indexes map first-party research and policy documents."""
    rss = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
      <title>FEDS Paper</title><link>https://www.federalreserve.gov/econres/feds/paper.htm</link>
      <guid>feds-1</guid><dc:creator>A. Economist</dc:creator>
      <description><![CDATA[<p>Abstract text</p>]]></description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate></item></channel></rss>"""
    html = b"""<html><body>
      <a href="/newsevents/pressreleases/monetary20240131a.htm">FOMC Statement January 31, 2024</a>
      <a href="/other.htm">Unrelated</a></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        content = rss if request.url.path.endswith("feds.xml") else html
        media = "application/xml" if content is rss else "text/html"
        return httpx.Response(200, content=content, headers={"content-type": media}, request=request)

    endpoints = (
        ("https://www.federalreserve.gov/feeds/feds.xml", "rss"),
        ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "html"),
    )
    connector = BoardFomcConnector(_client(handler), endpoints=endpoints)
    rss_page = connector.discover(cursor=None, limit=10)
    assert rss_page.records[0].authors == ("A. Economist",)
    assert rss_page.records[0].abstract == "Abstract text"
    assert rss_page.next_cursor == "1:0"
    fomc_page = connector.discover(cursor=rss_page.next_cursor, limit=10)
    assert fomc_page.records[0].published_at is not None
    assert fomc_page.records[0].authors == ("Federal Open Market Committee",)
    assert connector.discover(cursor="2:0", limit=1).records == ()


def test_board_rejects_malformed_feed_and_cursor() -> None:
    """Malformed first-party indexes enter a controlled acquisition failure."""
    connector = BoardFomcConnector(
        _client(lambda request: httpx.Response(200, content=b"<bad", request=request)),
        endpoints=(("https://www.federalreserve.gov/feeds/feds.xml", "rss"),),
    )
    with pytest.raises(AcquisitionError, match="malformed"):
        connector.discover(cursor=None, limit=1)
    with pytest.raises(ValueError, match="cursor"):
        connector.discover(cursor="bad", limit=1)


def test_boston_sitemap_discovery_filters_scope_and_dates() -> None:
    """Boston Fed discovery keeps scoped URLs and removes web filename suffixes from titles."""
    sitemap = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.bostonfed.org/publications/research-department-working-paper/2024/a-paper.Aspx</loc></url>
      <url><loc>https://www.bostonfed.org/publications/research-department-working-paper/2009/old</loc></url>
      <url><loc>https://www.bostonfed.org/about</loc></url></urlset>"""
    connector = BostonFedConnector(
        _client(
            lambda request: httpx.Response(
                200,
                content=sitemap,
                headers={"content-type": "application/xml"},
                request=request,
            )
        )
    )
    page = connector.discover(cursor=None, limit=10)
    assert len(page.records) == 1
    assert page.records[0].title == "A Paper"
    assert page.records[0].published_at is not None
    assert page.records[0].published_at.year == 2024
    with pytest.raises(ValueError, match="cursor"):
        connector.discover(cursor="bad", limit=1)


def test_discovery_non_success_and_json_serialization_edges() -> None:
    """All connectors reject source errors before mapping partial metadata."""
    connector = BostonFedConnector(_client(lambda request: httpx.Response(500, request=request)))
    with pytest.raises(AcquisitionError, match="non-success"):
        connector.discover(cursor=None, limit=1)
    assert json.loads(json.dumps({"ok": True}))["ok"] is True


def test_connector_mapping_edges_are_conservative() -> None:
    """Missing or malformed optional source fields remain absent instead of guessed."""
    core = _core_record(
        {
            "id": "edge",
            "title": "Edge record",
            "authors": [None, {"name": ""}],
            "yearPublished": "not-a-year",
            "updatedDate": "not-a-date",
            "sourceFulltextUrls": ["https://core.ac.uk/edge"],
        },
        _NOW,
    )
    assert core.published_at is None
    assert core.asserted_updated_at is None
    assert core.metadata_url == "https://core.ac.uk/edge"
    with pytest.raises(AcquisitionError, match="id or title"):
        _core_record({"id": ""}, _NOW)

    crossref = _crossref_record(
        {"URL": "https://example.org/item", "title": ["Title"], "published": {"date-parts": []}},
        _NOW,
    )
    assert crossref.published_at is None
    assert crossref.identifiers == ()
    with pytest.raises(AcquisitionError, match="title"):
        _crossref_record({"URL": "https://example.org/item"}, _NOW)

    assert _parse_rss_datetime("bad") is None
    assert _date_from_url_or_title("https://x/20241399", "bad") is None
    assert _handle_path("bad") == ""
    assert json.loads(redif_as_json(b"Template-Type: ReDIF-Paper 1.0\nTitle: T\n"))[0]["title"] == ["T"]
    with pytest.raises(AcquisitionError, match="malformed"):
        _sitemap_locations(b"<bad")


def test_connector_configuration_and_cursor_bounds() -> None:
    """Empty endpoints, negative cursors, and unbounded limits are rejected."""
    http = _client(lambda request: httpx.Response(200, content=b"", request=request))
    with pytest.raises(ValueError, match="endpoint"):
        RepecConnector(http, endpoints=())
    connector = RepecConnector(http, endpoints=("https://ideas.repec.org/RePEc/fip/test.rdf",))
    with pytest.raises(ValueError, match="limit"):
        connector.discover(cursor=None, limit=101)
    with pytest.raises(ValueError, match="negative"):
        connector.discover(cursor="-1:0", limit=1)
    board = BoardFomcConnector(http, endpoints=())
    assert board.discover(cursor=None, limit=1).records == ()
    with pytest.raises(ValueError, match="negative"):
        board.discover(cursor="-1:0", limit=1)


def test_repec_rsync_connector_uses_selected_official_series(tmp_path: Path) -> None:
    """The production RePEc path uses bounded rsync metadata rather than website scraping."""
    del tmp_path
    redif = (
        b"Template-Type: ReDIF-Paper 1.0\nTitle: Out-of-scope paper\nAuthor-Name: A\n"
        b"Handle: RePEc:fip:fedbwp:old\nCreation-Date: 2009\n\n"
        b"Template-Type: ReDIF-Paper 1.0\nTitle: Rsync paper\nAuthor-Name: A\n"
        b"Handle: RePEc:fip:fedbwp:1\nCreation-Date: 2024\n"
    )
    seen: list[str] = []

    def runner(endpoint: str, destination: Path) -> None:
        seen.append(endpoint)
        destination.write_bytes(redif)

    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    connector = RepecRsyncConnector(
        _client(lambda request: httpx.Response(200, request=request)),
        policy=PolicyEngine(registry),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("rsync"),
        endpoints=("rsync.repec.org::RePEc-ReDIF/fip/fedbwp/items.redif",),
        runner=runner,
    )
    page = connector.discover(cursor=None, limit=10)
    assert len(page.records) == 1
    assert page.records[0].title == "Rsync paper"
    assert page.traces[0].decisions[0].url.startswith("rsync://")
    assert seen == ["rsync.repec.org::RePEc-ReDIF/fip/fedbwp/items.redif"]
    with pytest.raises(ValueError, match="approved"):
        RepecRsyncConnector(
            _client(lambda request: httpx.Response(200, request=request)),
            policy=PolicyEngine(registry),
            clock=FrozenClock(_NOW),
            ids=DeterministicIdFactory("bad"),
            endpoints=("evil::path",),
        )


def test_repec_rsync_runner_uses_no_shell_and_wraps_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default rsync runner writes a bounded destination and normalizes process failures."""
    destination = tmp_path / "items.redif"
    executable = tmp_path / "bin" / "rsync"

    def success(arguments: list[str], **kwargs: object) -> None:
        assert arguments[0] == str(executable)
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert "shell" not in kwargs
        Path(arguments[-1]).write_bytes(b"metadata")

    monkeypatch.setattr(
        "economic_evidence_graph.connectors.repec._rsync_executable",
        lambda: executable,
    )
    monkeypatch.setattr("economic_evidence_graph.connectors.repec.subprocess.run", success)
    _run_rsync("rsync.repec.org::RePEc-ReDIF/fip/fedbwp/items.redif", destination)
    assert destination.read_bytes() == b"metadata"

    def failure(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("missing")

    monkeypatch.setattr("economic_evidence_graph.connectors.repec.subprocess.run", failure)
    with pytest.raises(AcquisitionError, match="rsync"):
        _run_rsync("rsync.repec.org::RePEc-ReDIF/fip/fedbwp/items.redif", destination)


def test_repec_rsync_executable_uses_fixed_absolute_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executable resolution never delegates an rsync path to ambient shell lookup."""

    def homebrew_only(candidate: Path) -> bool:
        return candidate == Path("/opt/homebrew/bin/rsync")

    monkeypatch.setattr(Path, "is_file", homebrew_only)
    assert _rsync_executable() == Path("/opt/homebrew/bin/rsync")
    monkeypatch.setattr(Path, "is_file", lambda _candidate: False)
    with pytest.raises(AcquisitionError, match="unavailable"):
        _rsync_executable()
