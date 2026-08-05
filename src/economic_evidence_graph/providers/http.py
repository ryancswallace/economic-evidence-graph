"""Governed HTTP transport with policy, robots, budgets, and SSRF controls."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from economic_evidence_graph.domain import (
    PolicyDecisionRecord,
    PolicyOutcome,
    RequestTrace,
    RobotsObservation,
)
from economic_evidence_graph.exceptions import AcquisitionError, PolicyDeniedError
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.ports import Clock, IdFactory

Resolver = Callable[[str], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class GovernedResponse:
    """Bounded response bytes and the complete governance trace."""

    requested_url: str
    resolved_url: str
    status_code: int
    media_type: str
    content: bytes
    retrieved_at: datetime
    trace: RequestTrace

    def json(self) -> Any:
        """Decode JSON using HTTPX's strict response decoder."""
        return httpx.Response(self.status_code, content=self.content).json()


@dataclass(frozen=True, slots=True)
class _RobotsRules:
    """Cached robots response for one origin."""

    url: str
    status_code: int
    content: bytes
    parser: urllib.robotparser.RobotFileParser | None


class GovernedHttpClient:
    """Synchronous HTTP client that cannot bypass source governance."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        registry: SourceRegistry,
        policy: PolicyEngine,
        clock: Clock,
        ids: IdFactory,
        crawler_email: str,
        resolver: Resolver | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        maximum_redirects: int = 5,
    ) -> None:
        if "@" not in crawler_email or maximum_redirects < 0:
            raise ValueError("a crawler contact email and nonnegative redirect limit are required")
        self._client = client
        self._registry = registry
        self._policy = policy
        self._clock = clock
        self._ids = ids
        self._resolver = resolver or _resolve_host
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._maximum_redirects = maximum_redirects
        self._user_agent = f"economic-evidence-graph/0.1 (+mailto:{crawler_email})"
        self._robots: dict[str, _RobotsRules] = {}
        self._last_request: dict[str, float] = {}
        self._lock = Lock()

    def get(
        self,
        *,
        source_code: str,
        action: str,
        url: str,
        content_class: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> GovernedResponse:
        """Get one resource after re-evaluating every redirect target."""
        policy = self._registry.get(source_code)
        if policy is None:
            raise PolicyDeniedError("source is not registered", context={"source_code": source_code})
        requested_url = str(httpx.URL(url, params=params))
        current_url = requested_url
        decisions: list[PolicyDecisionRecord] = []
        robots: list[RobotsObservation] = []
        request_headers = {"Accept": "*/*", "User-Agent": self._user_agent, **dict(headers or {})}

        for redirect_count in range(self._maximum_redirects + 1):
            _validate_public_url(current_url, self._resolver)
            evaluation = self._policy.evaluate(
                source_code=source_code,
                action=action,
                url=current_url,
                content_class=content_class,
            )
            decision = PolicyDecisionRecord(
                id=self._ids.new("pol"),
                source_code=source_code,
                policy_version=evaluation.policy_version,
                action=action,
                url=current_url,
                content_class=content_class,
                outcome=evaluation.outcome,
                reason=evaluation.reason,
                decided_at=self._clock.now(),
            )
            decisions.append(decision)
            if evaluation.outcome is PolicyOutcome.DENY:
                raise PolicyDeniedError(
                    "source policy denied HTTP request",
                    context={"source_code": source_code, "reason": evaluation.reason},
                    governance_trace=RequestTrace(tuple(decisions), tuple(robots)),
                    occurred_at=self._clock.now(),
                )

            try:
                observation = self._robots_decision(current_url)
            except AcquisitionError as error:
                raise AcquisitionError(
                    str(error),
                    code=error.code,
                    context=error.context,
                    governance_trace=RequestTrace(tuple(decisions), tuple(robots)),
                    occurred_at=self._clock.now(),
                ) from error
            robots.append(observation)
            if not observation.allowed:
                raise PolicyDeniedError(
                    "robots policy denied HTTP request",
                    context={"source_code": source_code, "reason": observation.reason},
                    governance_trace=RequestTrace(tuple(decisions), tuple(robots)),
                    occurred_at=self._clock.now(),
                )

            self._wait_for_budget(urlsplit(current_url).hostname or "", policy.requests_per_minute)
            try:
                response = self._bounded_get(current_url, request_headers, policy.maximum_response_bytes)
            except AcquisitionError as error:
                raise AcquisitionError(
                    str(error),
                    code=error.code,
                    context=error.context,
                    governance_trace=RequestTrace(tuple(decisions), tuple(robots)),
                    occurred_at=self._clock.now(),
                ) from error
            if response.status_code not in {301, 302, 303, 307, 308}:
                return GovernedResponse(
                    requested_url=requested_url,
                    resolved_url=str(response.url),
                    status_code=response.status_code,
                    media_type=(
                        response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip()
                    ),
                    content=response.content,
                    retrieved_at=self._clock.now(),
                    trace=RequestTrace(tuple(decisions), tuple(robots)),
                )
            location = response.headers.get("location")
            if location is None:
                raise AcquisitionError("redirect response omitted Location")
            current_url = urljoin(current_url, location)
            if redirect_count == self._maximum_redirects:
                raise AcquisitionError("HTTP redirect limit exceeded")
        raise AcquisitionError("HTTP redirect loop did not terminate")  # pragma: no cover

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _robots_decision(self, target_url: str) -> RobotsObservation:
        """Load and evaluate the target origin's robots policy."""
        parts = urlsplit(target_url)
        host = parts.hostname or ""
        port = "" if parts.port is None else f":{parts.port}"
        origin = f"{parts.scheme}://{host}{port}"
        rules = self._robots.get(origin)
        if rules is None:
            robots_url = f"{origin}/robots.txt"
            _validate_public_url(robots_url, self._resolver)
            response = self._bounded_get(robots_url, {"User-Agent": self._user_agent}, 1_048_576)
            parser: urllib.robotparser.RobotFileParser | None = None
            if 200 <= response.status_code < 300:
                parser = urllib.robotparser.RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
            rules = _RobotsRules(robots_url, response.status_code, response.content, parser)
            self._robots[origin] = rules

        if rules.parser is not None:
            allowed = rules.parser.can_fetch(self._user_agent, target_url)
            reason = "robots_allowed" if allowed else "robots_disallowed"
        elif rules.status_code in {404, 410}:
            allowed, reason = True, "robots_not_found"
        elif rules.status_code in {401, 403}:
            allowed, reason = False, "robots_access_denied"
        else:
            allowed, reason = False, "robots_unavailable_fail_closed"
        digest = hashlib.sha256(rules.content).hexdigest()
        return RobotsObservation(
            id=self._ids.new("rob"),
            host=host,
            robots_url=rules.url,
            target_url=target_url,
            observed_at=self._clock.now(),
            http_status=rules.status_code,
            content_sha256=digest,
            allowed=allowed,
            reason=reason,
        )

    def _bounded_get(self, url: str, headers: Mapping[str, str], maximum_bytes: int) -> httpx.Response:
        """Read a response without allowing unbounded buffering."""
        try:
            with self._client.stream("GET", url, headers=headers, follow_redirects=False) as response:
                declared = response.headers.get("content-length")
                if declared is not None and int(declared) > maximum_bytes:
                    raise AcquisitionError("response exceeds configured byte limit")
                chunks: list[bytes] = []
                length = 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > maximum_bytes:
                        raise AcquisitionError("response exceeds configured byte limit")
                    chunks.append(chunk)
                # iter_bytes() returns decoded representation bytes.  Do not carry
                # representation/transfer framing into the materialized response or
                # HTTPX will attempt to decode the body a second time.
                materialized_headers = [
                    (name, value)
                    for name, value in response.headers.multi_items()
                    if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
                ]
                return httpx.Response(
                    response.status_code,
                    headers=materialized_headers,
                    content=b"".join(chunks),
                    request=response.request,
                )
        except AcquisitionError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise AcquisitionError("HTTP request failed") from error

    def _wait_for_budget(self, host: str, requests_per_minute: int) -> None:
        """Serialize a per-host fixed-interval request budget."""
        interval = 60.0 / requests_per_minute
        with self._lock:
            now = self._monotonic()
            prior = self._last_request.get(host)
            if prior is not None and now - prior < interval:
                self._sleeper(interval - (now - prior))
                now = self._monotonic()
            self._last_request[host] = now


def _resolve_host(host: str) -> tuple[str, ...]:
    """Resolve a host through the operating system resolver."""
    try:
        return tuple(sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)}))
    except socket.gaierror as error:
        raise AcquisitionError("host could not be resolved") from error


def _validate_public_url(url: str, resolver: Resolver) -> None:
    """Reject ambiguous schemes, local names, and non-public network targets."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None:
        raise AcquisitionError("URL is not an uncredentialed HTTP(S) target")
    if host == "localhost" or host.endswith(".local"):
        raise AcquisitionError("local network targets are denied")
    try:
        addresses = (str(ipaddress.ip_address(host)),)
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise AcquisitionError("host resolved without an address")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise AcquisitionError("non-public network targets are denied")
