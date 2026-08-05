"""Source-policy and governed-transport contract tests."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from economic_evidence_graph.domain import PolicyOutcome
from economic_evidence_graph.exceptions import AcquisitionError, ConfigurationError, PolicyDeniedError
from economic_evidence_graph.policy import PolicyEngine, SourcePolicy, SourceRegistry
from economic_evidence_graph.providers import DeterministicIdFactory, FrozenClock, GovernedHttpClient
from economic_evidence_graph.providers.http import _validate_public_url

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)


def _registry() -> SourceRegistry:
    """Load the checked-in approved pilot registry."""
    return SourceRegistry.load(_ROOT / "config/source-registry.toml")


def _http(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    registry: SourceRegistry | None = None,
    maximum_redirects: int = 5,
) -> GovernedHttpClient:
    """Build a deterministic governed client around an HTTPX mock transport."""
    return GovernedHttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        registry=registry or _registry(),
        policy=PolicyEngine(registry or _registry()),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("http"),
        crawler_email="crawler@example.org",
        resolver=lambda _host: ("93.184.216.34",),
        sleeper=lambda _seconds: None,
        monotonic=lambda: 1.0,
        maximum_redirects=maximum_redirects,
    )


def test_policy_engine_allows_internal_use_and_denies_delivery() -> None:
    """Unknown rights permit internal processing but not full-text delivery."""
    registry = _registry()
    engine = PolicyEngine(registry)

    fetch = engine.evaluate(
        source_code="crossref",
        action="fetch_content",
        url="https://publisher.example/paper.pdf",
        content_class="linked_full_text",
    )
    store = engine.evaluate(
        source_code="crossref",
        action="store_raw",
        url="https://publisher.example/paper.pdf",
        content_class="linked_full_text",
    )
    display = engine.evaluate(
        source_code="crossref",
        action="display_full_text",
        url="https://api.crossref.org/works/1",
        content_class="linked_full_text",
    )
    snippet = engine.evaluate(
        source_code="crossref",
        action="display_snippet",
        url="https://api.crossref.org/works/1",
        content_class="linked_full_text",
        attributed=True,
    )

    assert fetch.outcome is store.outcome is PolicyOutcome.ALLOW
    assert display.outcome is PolicyOutcome.DENY
    assert snippet.outcome is PolicyOutcome.ALLOW
    assert (
        engine.evaluate(
            source_code="crossref",
            action="display_snippet",
            url="https://api.crossref.org/works/1",
            content_class="text",
        ).reason
        == "action_condition_not_met"
    )
    assert registry.get("missing") is None
    assert [item.code for item in registry.policies] == sorted(item.code for item in registry.policies)
    assert (
        engine.evaluate(
            source_code="repec",
            action="store_metadata",
            url="rsync://rsync.repec.org/RePEc-ReDIF/fip/fedbwp/items.redif",
            content_class="bibliographic_metadata",
        ).outcome
        is PolicyOutcome.ALLOW
    )
    assert (
        engine.evaluate(
            source_code="repec",
            action="fetch_content",
            url="rsync://rsync.repec.org/RePEc-ReDIF/fip/fedbwp/paper.pdf",
            content_class="linked_full_text",
        ).outcome
        is PolicyOutcome.DENY
    )


def test_policy_engine_default_deny_host_path_source_and_url() -> None:
    """Every absent source, action, host, path, and malformed URL fails closed."""
    engine = PolicyEngine(_registry())
    cases = (
        ("missing", "discover_metadata", "https://api.crossref.org/works", "source_not_registered"),
        ("crossref", "discover_metadata", "ftp://api.crossref.org/works", "invalid_url"),
        ("crossref", "discover_metadata", "https://evil.example/works", "host_not_allowed"),
        ("crossref", "discover_metadata", "https://api.crossref.org/members", "path_not_allowed"),
        ("crossref", "unknown_action", "https://api.crossref.org/works", "action_denied"),
    )
    for source, action, url, reason in cases:
        assert engine.evaluate(source_code=source, action=action, url=url, content_class="metadata").reason == reason

    base = _registry().get("crossref")
    assert base is not None
    disabled = (
        SourcePolicy(**{**base.__dict__, "enabled": False})
        if hasattr(base, "__dict__")
        else SourcePolicy(
            code=base.code,
            display_name=base.display_name,
            connector=base.connector,
            policy_version=base.policy_version,
            enabled=False,
            review_status=base.review_status,
            hosts=base.hosts,
            path_prefixes=base.path_prefixes,
            allow_external_content_hosts=base.allow_external_content_hosts,
            requests_per_minute=base.requests_per_minute,
            concurrency=base.concurrency,
            maximum_response_bytes=base.maximum_response_bytes,
            actions=base.actions,
        )
    )
    custom = SourceRegistry(schema_version="v1", default_action="deny", policies=(disabled,))
    assert (
        PolicyEngine(custom)
        .evaluate(
            source_code="crossref",
            action="discover_metadata",
            url="https://api.crossref.org/works",
            content_class="metadata",
        )
        .reason
        == "source_not_approved"
    )


@pytest.mark.parametrize(
    "document",
    [
        "default_action='allow'\nschema_version='v'\n[[sources]]\n",
        "default_action='deny'\nschema_version='v'\nsources='bad'\n",
        "default_action='deny'\nschema_version='v'\n[[sources]]\ncode='x'\n",
    ],
)
def test_source_registry_rejects_invalid_documents(tmp_path: Path, document: str) -> None:
    """Malformed or incomplete policy documents cannot enter service."""
    path = tmp_path / "registry.toml"
    path.write_text(document)
    with pytest.raises(ConfigurationError):
        SourceRegistry.load(path)
    with pytest.raises(ConfigurationError):
        SourceRegistry.load(tmp_path / "missing.toml")


def test_source_registry_rejects_duplicate_and_invalid_rules() -> None:
    """Registry construction and action validation preserve one unambiguous policy."""
    policy = _registry().get("crossref")
    assert policy is not None
    with pytest.raises(ConfigurationError, match="unique"):
        SourceRegistry(schema_version="v", default_action="deny", policies=(policy, policy))
    with pytest.raises(ConfigurationError):
        SourceRegistry(schema_version="", default_action="deny", policies=(policy,))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('hosts = ["api.example.org"]', 'hosts = ["API.EXAMPLE.ORG"]'),
        ('path_prefixes = ["/"]', 'path_prefixes = ["works"]'),
        ("enabled = true", 'enabled = "yes"'),
        ("policy_version = 1", "policy_version = 0"),
        ('rule = "deny"', 'rule = "pending"'),
        ("[sources.actions]", 'actions = "bad"'),
    ],
)
def test_source_registry_field_validation(tmp_path: Path, old: str, new: str) -> None:
    """Typed registry controls reject ambiguous values before evaluation."""
    body = """schema_version = "v"
default_action = "deny"
[[sources]]
code = "example"
display_name = "Example"
connector = "example"
policy_version = 1
enabled = true
review_status = "approved_internal_pilot"
hosts = ["api.example.org"]
path_prefixes = ["/"]
allow_external_content_hosts = false
requests_per_minute = 1
concurrency = 1
maximum_response_bytes = 1
[sources.actions]
rule = "deny"
"""
    path = tmp_path / "bad.toml"
    path.write_text(body.replace(old, new))
    with pytest.raises(ConfigurationError):
        SourceRegistry.load(path)


def test_governed_http_records_robots_policy_redirect_and_json() -> None:
    """Successful requests preserve every redirect decision and one robots result per hop."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
        if request.url.path == "/works":
            return httpx.Response(302, headers={"Location": "/works/final"}, request=request)
        return httpx.Response(200, json={"ok": True}, headers={"Content-Type": "application/json"}, request=request)

    client = _http(handler)
    result = client.get(
        source_code="crossref",
        action="discover_metadata",
        url="https://api.crossref.org/works",
        content_class="metadata",
        params={"rows": 1},
    )
    assert result.json() == {"ok": True}
    assert result.resolved_url.endswith("/works/final")
    assert len(result.trace.decisions) == len(result.trace.robots) == 2
    assert result.trace.robots[0].reason == "robots_allowed"
    assert any("rows=1" in request for request in requests)
    client.close()


@pytest.mark.parametrize(
    ("robots_status", "robots_text", "expected_reason"),
    [
        (200, "User-agent: *\nDisallow: /works\n", "robots_disallowed"),
        (403, "", "robots_access_denied"),
        (503, "", "robots_unavailable_fail_closed"),
    ],
)
def test_governed_http_fails_closed_for_robots(
    robots_status: int,
    robots_text: str,
    expected_reason: str,
) -> None:
    """Disallowed, forbidden, and unavailable robots policies stop acquisition."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(robots_status, text=robots_text, request=request)

    with pytest.raises(PolicyDeniedError) as captured:
        _http(handler).get(
            source_code="crossref",
            action="discover_metadata",
            url="https://api.crossref.org/works",
            content_class="metadata",
        )
    assert captured.value.context["reason"] == expected_reason
    assert captured.value.governance_trace is not None
    assert captured.value.governance_trace.decisions[0].outcome is PolicyOutcome.ALLOW
    assert captured.value.occurred_at == _NOW


def test_governed_http_allows_missing_robots_and_external_full_text() -> None:
    """A 404 robots file permits an otherwise approved external content request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=b"paper", headers={"content-type": "text/plain"}, request=request)

    result = _http(handler).get(
        source_code="crossref",
        action="fetch_content",
        url="https://publisher.example/paper.txt",
        content_class="linked_full_text",
    )
    assert result.content == b"paper"
    assert result.trace.robots[0].reason == "robots_not_found"


def test_governed_http_materializes_compressed_content_once() -> None:
    """A streamed encoded response is decoded once and bounded by decoded size."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            content=gzip.compress(b'{"ok":true}'),
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            request=request,
        )

    result = _http(handler).get(
        source_code="crossref",
        action="discover_metadata",
        url="https://api.crossref.org/works",
        content_class="metadata",
    )
    assert result.json() == {"ok": True}


def test_governed_http_denies_policy_size_redirect_and_network_failures() -> None:
    """Transport bounds and policy checks fail with stable application exceptions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        if request.url.path == "/works":
            return httpx.Response(302, headers={"Location": "/works"}, request=request)
        return httpx.Response(200, headers={"content-length": "999999999"}, request=request)

    with pytest.raises(PolicyDeniedError):
        _http(handler).get(
            source_code="boston_fed",
            action="discover_metadata",
            url="https://evil.example/publications/x",
            content_class="metadata",
        )
    with pytest.raises(AcquisitionError, match="redirect"):
        _http(handler, maximum_redirects=0).get(
            source_code="crossref",
            action="discover_metadata",
            url="https://api.crossref.org/works",
            content_class="metadata",
        )

    def oversized(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=b"x" * 60_000_000, request=request)

    with pytest.raises(AcquisitionError, match="byte limit"):
        _http(oversized).get(
            source_code="crossref",
            action="fetch_content",
            url="https://publisher.example/paper.pdf",
            content_class="linked_full_text",
        )


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "http://localhost/x", "http://service.local/x", "http://127.0.0.1/x"],
)
def test_ssrf_guard_rejects_local_and_ambiguous_targets(url: str) -> None:
    """URL validation prevents connector access to local resources."""
    with pytest.raises(AcquisitionError):
        _validate_public_url(url, lambda _host: ("93.184.216.34",))
    with pytest.raises(AcquisitionError, match="address"):
        _validate_public_url("https://example.org/x", lambda _host: ())
    with pytest.raises(AcquisitionError, match="non-public"):
        _validate_public_url("https://example.org/x", lambda _host: ("10.0.0.1",))
