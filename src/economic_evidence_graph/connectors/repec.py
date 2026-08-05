"""RePEc ReDIF metadata connector for selected Federal Reserve series."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from economic_evidence_graph.connectors.base import GovernedConnector, require_success
from economic_evidence_graph.domain import (
    ContentLink,
    IdentifierValue,
    PolicyDecisionRecord,
    PolicyOutcome,
    RequestTrace,
)
from economic_evidence_graph.exceptions import AcquisitionError, PolicyDeniedError
from economic_evidence_graph.policy import PolicyEngine
from economic_evidence_graph.ports import Clock, DiscoveryPage, DiscoveryRecord, IdFactory
from economic_evidence_graph.providers.http import GovernedHttpClient

_DEFAULT_ENDPOINTS = (
    "https://ideas.repec.org/RePEc/fip/fedbwp.rdf",
    "https://ideas.repec.org/RePEc/fip/fedgfe.rdf",
    "https://ideas.repec.org/RePEc/fip/fedgif.rdf",
)
_DEFAULT_RSYNC_ENDPOINTS = (
    "rsync.repec.org::RePEc-ReDIF/fip/fedbwp/items.redif",
    "rsync.repec.org::RePEc-ReDIF/fip/fedgfe/items.redif",
    "rsync.repec.org::RePEc-ReDIF/fip/fedgif/items.redif",
)
_RSYNC_PATTERN = re.compile(r"^rsync\.repec\.org::RePEc-ReDIF/fip/[a-z0-9]+/items\.redif$")
_PILOT_START_YEAR = 2010
RsyncRunner = Callable[[str, Path], None]


class RepecConnector(GovernedConnector):
    """Read publisher-supplied ReDIF rather than scraping RePEc result pages."""

    source_code = "repec"

    def __init__(self, http: GovernedHttpClient, *, endpoints: tuple[str, ...] = _DEFAULT_ENDPOINTS) -> None:
        super().__init__(http)
        if not endpoints:
            raise ValueError("at least one ReDIF endpoint is required")
        self._endpoints = endpoints

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover a resumable slice from one selected ReDIF series file."""
        if not 1 <= limit <= 100:
            raise ValueError("connector limit must be between 1 and 100")
        endpoint_index, offset = _cursor(cursor)
        if endpoint_index >= len(self._endpoints):
            return DiscoveryPage((), None, None, ())
        endpoint = self._endpoints[endpoint_index]
        response = self._http.get(
            source_code=self.source_code,
            action="discover_metadata",
            url=endpoint,
            content_class="redif_metadata",
        )
        require_success(response.status_code, source_code=self.source_code)
        templates = _pilot_templates(response.content)
        records = tuple(
            _redif_record(fields, raw, endpoint, response.retrieved_at)
            for fields, raw in templates[offset : offset + limit]
        )
        next_offset = offset + len(records)
        if next_offset < len(templates):
            next_cursor = f"{endpoint_index}:{next_offset}"
        elif endpoint_index + 1 < len(self._endpoints):
            next_cursor = f"{endpoint_index + 1}:0"
        else:
            next_cursor = None
        return DiscoveryPage(records, next_cursor, str(len(templates)), (response.trace,))


class RepecRsyncConnector(GovernedConnector):
    """Acquire selected ReDIF series through RePEc's public rsync service."""

    source_code = "repec"

    def __init__(
        self,
        http: GovernedHttpClient,
        *,
        policy: PolicyEngine,
        clock: Clock,
        ids: IdFactory,
        endpoints: tuple[str, ...] = _DEFAULT_RSYNC_ENDPOINTS,
        runner: RsyncRunner | None = None,
    ) -> None:
        super().__init__(http)
        if not endpoints or any(_RSYNC_PATTERN.fullmatch(endpoint) is None for endpoint in endpoints):
            raise ValueError("RePEc rsync endpoints must be approved selected-series paths")
        self._policy = policy
        self._clock = clock
        self._ids = ids
        self._endpoints = endpoints
        self._runner = runner or _run_rsync

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        """Discover a bounded slice from the official RePEc ReDIF mirror."""
        if not 1 <= limit <= 100:
            raise ValueError("connector limit must be between 1 and 100")
        endpoint_index, offset = _cursor(cursor)
        if endpoint_index >= len(self._endpoints):
            return DiscoveryPage((), None, None, ())
        endpoint = self._endpoints[endpoint_index]
        audit_url = "rsync://" + endpoint.replace("::", "/", 1)
        evaluation = self._policy.evaluate(
            source_code=self.source_code,
            action="discover_metadata",
            url=audit_url,
            content_class="redif_metadata",
        )
        decision = PolicyDecisionRecord(
            id=self._ids.new("pol"),
            source_code=self.source_code,
            policy_version=evaluation.policy_version,
            action="discover_metadata",
            url=audit_url,
            content_class="redif_metadata",
            outcome=evaluation.outcome,
            reason=evaluation.reason,
            decided_at=self._clock.now(),
        )
        if evaluation.outcome is PolicyOutcome.DENY:
            raise PolicyDeniedError("source policy denied RePEc rsync discovery")
        with tempfile.TemporaryDirectory(prefix="eeg-repec-") as temporary:
            destination = Path(temporary) / "items.redif"
            self._runner(endpoint, destination)
            if not destination.is_file() or destination.stat().st_size > 10_000_000:
                raise AcquisitionError("RePEc rsync output is absent or exceeds the selected-series limit")
            content = destination.read_bytes()
        templates = _pilot_templates(content)
        records = tuple(
            _redif_record(fields, raw, audit_url, self._clock.now())
            for fields, raw in templates[offset : offset + limit]
        )
        next_offset = offset + len(records)
        if next_offset < len(templates):
            next_cursor = f"{endpoint_index}:{next_offset}"
        elif endpoint_index + 1 < len(self._endpoints):
            next_cursor = f"{endpoint_index + 1}:0"
        else:
            next_cursor = None
        return DiscoveryPage(records, next_cursor, str(len(templates)), (RequestTrace((decision,), ()),))


def parse_redif(content: bytes) -> tuple[dict[str, tuple[str, ...]], ...]:
    """Parse ReDIF templates, retaining repeated fields and continuations."""
    return tuple(fields for fields, _raw in _parse_redif_with_raw(content))


def _parse_redif_with_raw(content: bytes) -> tuple[tuple[dict[str, tuple[str, ...]], bytes], ...]:
    """Parse templates while preserving exact input bytes for each observation."""
    text = _decode_redif(content)
    blocks = re.split(r"(?:\r?\n){2,}", text)
    parsed: list[tuple[dict[str, tuple[str, ...]], bytes]] = []
    for block in blocks:
        if not block.strip():
            continue
        values: dict[str, list[str]] = {}
        last_key: str | None = None
        for line in block.splitlines():
            if line[:1].isspace() and last_key is not None:
                values[last_key][-1] = f"{values[last_key][-1]} {line.strip()}".strip()
                continue
            if ":" not in line:
                last_key = None
                continue
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower()
            last_key = normalized_key
            values.setdefault(normalized_key, []).append(value.strip())
        if "template-type" in values:
            parsed.append(({key: tuple(items) for key, items in values.items()}, block.encode("utf-8")))
    return tuple(parsed)


def _pilot_templates(content: bytes) -> tuple[tuple[dict[str, tuple[str, ...]], bytes], ...]:
    """Retain only records whose source date proves they are in the pilot window."""
    return tuple(
        (fields, raw)
        for fields, raw in _parse_redif_with_raw(content)
        if (published := _redif_date(fields)) is not None and published.year >= _PILOT_START_YEAR
    )


def _redif_record(
    fields: dict[str, tuple[str, ...]],
    raw: bytes,
    endpoint: str,
    observed_at: datetime,
) -> DiscoveryRecord:
    """Map a paper/article ReDIF template to source metadata."""
    handle = _first(fields, "handle")
    title = _first(fields, "title")
    if handle is None or title is None:
        raise AcquisitionError("ReDIF record omitted Handle or Title")
    doi = _first(fields, "doi")
    formats = fields.get("file-format", ())
    functions = fields.get("file-function", ())
    links = tuple(
        ContentLink(
            url,
            formats[index] if index < len(formats) else None,
            "linked_full_text" if index >= len(functions) or "full" in functions[index].lower() else "linked_content",
            None,
        )
        for index, url in enumerate(fields.get("file-url", ()))
        if url.startswith(("http://", "https://"))
    )
    metadata_url = f"https://ideas.repec.org/{_handle_path(handle)}.html"
    identifiers = [IdentifierValue("repec", handle)]
    if doi is not None:
        identifiers.append(IdentifierValue("doi", doi.lower()))
    return DiscoveryRecord(
        source_code="repec",
        source_identifier=handle,
        metadata_url=metadata_url,
        title=title,
        authors=fields.get("author-name", ()),
        abstract=_first(fields, "abstract"),
        published_at=_redif_date(fields),
        asserted_updated_at=None,
        identifiers=tuple(identifiers),
        content_links=links,
        raw_metadata=raw,
        metadata_media_type="text/plain",
        discovered_at=observed_at,
    )


def _decode_redif(content: bytes) -> str:
    """Honor modern UTF-8 and legacy ReDIF Windows encodings."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def _cursor(value: str | None) -> tuple[int, int]:
    """Decode an endpoint-and-offset cursor."""
    if value is None:
        return 0, 0
    try:
        endpoint, offset = (int(part) for part in value.split(":", 1))
    except ValueError as error:
        raise ValueError("RePEc cursor must be endpoint:offset") from error
    if endpoint < 0 or offset < 0:
        raise ValueError("RePEc cursor values must not be negative")
    return endpoint, offset


def _first(fields: dict[str, tuple[str, ...]], key: str) -> str | None:
    """Return the first non-empty ReDIF value."""
    values = fields.get(key, ())
    return values[0] if values and values[0] else None


def _redif_date(fields: dict[str, tuple[str, ...]]) -> date | None:
    """Read the best asserted ReDIF date without guessing precision."""
    value = _first(fields, "creation-date") or _first(fields, "year")
    if value is None:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", value)
    if match is None:
        return None
    year = int(match.group())
    month_match = re.search(r"-(\d{1,2})(?:-|$)", value)
    month = int(month_match.group(1)) if month_match else 1
    try:
        return date(year, month, 1)
    except ValueError:
        return None


def _handle_path(handle: str) -> str:
    """Map a RePEc handle to the public item-page path."""
    parts = handle.split(":")
    if len(parts) < 4:
        return ""
    return "/".join(("p", parts[1], parts[2], parts[3]))


def redif_as_json(content: bytes) -> bytes:
    """Return parsed ReDIF as canonical JSON for diagnostics."""
    return json.dumps(parse_redif(content), sort_keys=True, separators=(",", ":")).encode()


def _run_rsync(endpoint: str, destination: Path) -> None:
    """Run rsync without a shell, inherited credentials, or unbounded output."""
    try:
        executable = _rsync_executable()
        subprocess.run(  # nosec B603
            [str(executable), "--timeout=30", "--contimeout=15", endpoint, str(destination)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AcquisitionError("RePEc rsync acquisition failed") from error


def _rsync_executable() -> Path:
    """Resolve rsync only from known absolute installation paths."""
    for candidate in (Path("/usr/bin/rsync"), Path("/opt/homebrew/bin/rsync"), Path("/usr/local/bin/rsync")):
        if candidate.is_file():
            return candidate
    raise AcquisitionError("RePEc rsync executable is unavailable")
