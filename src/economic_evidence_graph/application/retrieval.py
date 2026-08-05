"""Strict historical hybrid retrieval with explainable fusion and provenance gates."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

from economic_evidence_graph.domain import (
    ChannelContribution,
    EvidenceSnippet,
    IndexGeneration,
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchRun,
    TemporalResult,
    TemporalSafetyClass,
)
from economic_evidence_graph.exceptions import ConflictError, NotFoundError, PolicyDeniedError
from economic_evidence_graph.policy import PolicyEngine
from economic_evidence_graph.ports import Clock, IdFactory
from economic_evidence_graph.ports.search import EmbeddingProvider, SearchIndex, SearchIndexFilters, SearchRepository
from economic_evidence_graph.serialization import record_sha256, to_json_value

_PHRASE_PATTERN = re.compile(r'"([^"\n]{1,200})"')
_TERM_PATTERN = re.compile(r"[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)?")
_RRF_K = 60
_MAX_CANDIDATES = 500
_CHANNEL_WEIGHTS = {
    "lexical_work": 1.0,
    "lexical_evidence": 1.2,
    "semantic_work": 0.8,
    "semantic_evidence": 1.0,
    "structured_work": 0.3,
}


@dataclass(slots=True)
class _Candidate:
    """Mutable fusion state internal to one request."""

    document: dict[str, object]
    score: float
    contributions: list[ChannelContribution]
    best_evidence: dict[str, object] | None = None
    best_evidence_contribution: float = -1.0


class RetrievalService:
    """Execute bounded hybrid search against one activated generation."""

    def __init__(
        self,
        *,
        repository: SearchRepository,
        index: SearchIndex,
        embeddings: EmbeddingProvider,
        policy: PolicyEngine,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._repository = repository
        self._index = index
        self._embeddings = embeddings
        self._policy = policy
        self._clock = clock
        self._ids = ids

    def search(self, request: SearchRequest) -> SearchResponse:
        """Search one immutable generation with pre-ranking historical filters."""
        generation = self._repository.latest_index_generation()
        if generation is None:
            raise ConflictError("search index has not been built; run `eeg rebuild-search`")
        request_hash = _request_hash(request)
        offset = _decode_cursor(request.cursor, generation.id, request_hash)
        filters = SearchIndexFilters(
            source_codes=request.filters.source_codes,
            published_from=request.filters.published_from,
            published_to=request.filters.published_to,
            eligible_at=None if request.historical_cutoff is None else request.historical_cutoff.at,
            allowed_safety_classes=(TemporalSafetyClass.SOURCE_FACT.value, TemporalSafetyClass.DETERMINISTIC.value),
        )
        candidates: dict[str, _Candidate] = {}
        suppressions = frozenset(self._repository.list_suppression_scopes())
        used: list[str] = []
        omitted: list[str] = []
        candidate_limit = _MAX_CANDIDATES
        for mode in request.modes:
            if mode is SearchMode.STRUCTURED and not _has_structured_filters(request.filters):
                omitted.append("structured:no_filters")
                continue
            if (
                mode is SearchMode.SEMANTIC
                and request.historical_cutoff is not None
                and self._embeddings.temporal_safety_class
                not in {TemporalSafetyClass.SOURCE_FACT, TemporalSafetyClass.DETERMINISTIC}
            ):
                omitted.append(f"semantic:{self._embeddings.temporal_safety_class.value}_not_certified_for_strict")
                continue
            query_vector = self._embeddings.embed(request.query) if mode is SearchMode.SEMANTIC else None
            index_kinds = ("work",) if mode is SearchMode.STRUCTURED else ("work", "evidence")
            for index_kind in index_kinds:
                channel = f"{mode.value}_{index_kind}"
                hits = self._index.search(
                    index_kind=index_kind,
                    channel=mode.value,
                    query=request.query,
                    query_vector=query_vector,
                    filters=filters,
                    limit=candidate_limit,
                )
                used.append(channel)
                self._fuse(candidates, hits, channel, index_kind, suppressions)

        selected = _collapse(candidates, request.collapse_versions)
        page = selected[offset : offset + request.limit]
        parsed_terms = _parsed_terms(request.query)
        results = tuple(
            self._result(
                candidate,
                rank=offset + rank,
                cutoff=None if request.historical_cutoff is None else request.historical_cutoff.at,
                parsed_terms=parsed_terms,
            )
            for rank, candidate in enumerate(page, start=1)
        )
        next_cursor = None
        if offset + request.limit < len(selected):
            next_cursor = _encode_cursor(generation.id, request_hash, offset + request.limit)
        warnings = [
            "semantic retrieval uses a deterministic T1 economics-aware baseline, not a learned embedding model"
        ]
        if request.historical_cutoff is None:
            warnings.append("no historical cutoff was requested; results reflect the active corpus")
        response = SearchResponse(
            query=request.query,
            parsed_terms=parsed_terms,
            results=results,
            index_generation_id=generation.id,
            corpus_snapshot_id=generation.corpus_snapshot_id,
            retrieval_config_id=generation.retrieval_config_id,
            modes_used=tuple(used),
            omitted_channels=tuple(omitted),
            warnings=tuple(warnings),
            next_cursor=next_cursor,
            search_run_id=None,
        )
        if not request.save_run:
            return response
        run_id = self._ids.new("srchrun")
        response = SearchResponse(
            query=response.query,
            parsed_terms=response.parsed_terms,
            results=response.results,
            index_generation_id=response.index_generation_id,
            corpus_snapshot_id=response.corpus_snapshot_id,
            retrieval_config_id=response.retrieval_config_id,
            modes_used=response.modes_used,
            omitted_channels=response.omitted_channels,
            warnings=response.warnings,
            next_cursor=response.next_cursor,
            search_run_id=run_id,
        )
        request_payload = _object(request)
        response_payload = _object(response)
        self._repository.put_search_run(
            SearchRun(
                id=run_id,
                request_payload=request_payload,
                response_payload=response_payload,
                index_generation_id=generation.id,
                query_sha256=record_sha256(request.query.strip()),
                created_at=self._clock.now(),
            )
        )
        return response

    def saved_run(self, run_id: str) -> SearchRun:
        """Return an immutable saved request/response manifest."""
        item = self._repository.get_search_run(run_id)
        if item is None:
            raise NotFoundError("search run not found", context={"run_id": run_id})
        return item

    def evidence(self, evidence_unit_id: str) -> EvidenceSnippet:
        """Return one policy-gated evidence unit from the active generation."""
        document = self._index.get_evidence(evidence_unit_id)
        if document is None:
            raise NotFoundError("search evidence unit not found", context={"evidence_unit_id": evidence_unit_id})
        if _is_suppressed(document, frozenset(self._repository.list_suppression_scopes())):
            raise PolicyDeniedError("this evidence unit is suppressed from delivery")
        snippet = self._snippet(document)
        if snippet is None:
            raise PolicyDeniedError("source policy denies delivery of this evidence excerpt")
        return snippet

    def work_versions(self, work_id: str, *, limit: int = 50) -> tuple[dict[str, object], ...]:
        """Return current unsuppressed version projections for one exact work."""
        if not 1 <= limit <= 50:
            raise ValueError("work version limit must be between 1 and 50")
        suppressions = frozenset(self._repository.list_suppression_scopes())
        return tuple(
            item
            for item in self._index.get_work_versions(work_id, limit=limit)
            if not _is_suppressed(item, suppressions)
        )

    def version_evidence(self, document_version_id: str, *, limit: int = 100) -> tuple[EvidenceSnippet, ...]:
        """Return policy-gated evidence for one exact version under a hard bound."""
        if not 1 <= limit <= 200:
            raise ValueError("version evidence limit must be between 1 and 200")
        suppressions = frozenset(self._repository.list_suppression_scopes())
        snippets: list[EvidenceSnippet] = []
        for document in self._index.list_version_evidence(document_version_id, limit=limit):
            if _is_suppressed(document, suppressions):
                continue
            snippet = self._snippet(document)
            if snippet is not None:
                snippets.append(snippet)
        return tuple(snippets)

    def active_generation(self) -> IndexGeneration:
        """Return the active immutable generation for interface provenance."""
        generation = self._repository.latest_index_generation()
        if generation is None:
            raise ConflictError("search index has not been built; run `eeg rebuild-search`")
        return generation

    def _fuse(
        self,
        candidates: dict[str, _Candidate],
        hits: tuple[object, ...],
        channel: str,
        index_kind: str,
        suppressions: frozenset[tuple[str, str]],
    ) -> None:
        """Accumulate weighted reciprocal-rank contributions by version."""
        from economic_evidence_graph.ports import SearchIndexHit

        weight = _CHANNEL_WEIGHTS[channel]
        for raw_hit in hits:
            hit = cast(SearchIndexHit, raw_hit)
            if _is_suppressed(hit.document, suppressions):
                continue
            version_id = _required_text(hit.document, "document_version_id")
            contribution = weight / (_RRF_K + hit.rank)
            item = candidates.get(version_id)
            if item is None:
                item = _Candidate(hit.document, 0.0, [])
                candidates[version_id] = item
            if any(value.channel == channel for value in item.contributions):
                continue
            if index_kind == "work" or item.document.get("title") is None:
                item.document = hit.document
            item.score += contribution
            item.contributions.append(ChannelContribution(channel, hit.rank, hit.score, contribution))
            if index_kind == "evidence" and contribution > item.best_evidence_contribution:
                item.best_evidence = hit.document
                item.best_evidence_contribution = contribution

    def _result(
        self,
        candidate: _Candidate,
        *,
        rank: int,
        cutoff: datetime | None,
        parsed_terms: tuple[str, ...],
    ) -> SearchResult:
        """Apply final delivery/provenance gates and explain one candidate."""
        document = candidate.document
        evidence = None if candidate.best_evidence is None else self._snippet(candidate.best_evidence)
        warnings = list(_string_tuple(document.get("quality_warnings")))
        if candidate.best_evidence is not None and evidence is None:
            warnings.append("matched evidence was withheld by source delivery policy")
        if cutoff is not None and candidate.best_evidence is None:
            warnings.append("no cutoff-eligible evidence excerpt is available; result is metadata-only")
        eligible_from = _required_datetime(document, "eligible_from")
        matched = tuple(term for term in parsed_terms if term.casefold() in _searchable_text(candidate).casefold())
        return SearchResult(
            rank=rank,
            score=candidate.score,
            work_id=_required_text(document, "work_id"),
            document_version_id=_required_text(document, "document_version_id"),
            source_record_id=_required_text(document, "source_record_id"),
            title=_required_text(document, "title"),
            authors=_string_tuple(document.get("authors")),
            published_at=_optional_date(document.get("published_at")),
            source_code=_required_text(document, "source_code"),
            source_identifier=_required_text(document, "source_identifier"),
            source_url=_required_text(document, "source_url"),
            policy_version=_required_int(document, "policy_version"),
            alternative_version_count=max(0, _required_int(document, "version_count") - 1),
            version_selection_reason="highest fused score among eligible source versions",
            contributions=tuple(sorted(candidate.contributions, key=lambda item: (item.channel, item.rank))),
            matched_phrases=matched,
            evidence=evidence,
            temporal=TemporalResult(
                cutoff=cutoff,
                eligible=cutoff is None or eligible_from <= cutoff,
                eligible_from=eligible_from,
                date_basis=_required_text(document, "date_basis"),
                date_evidence=_required_text(document, "date_evidence"),
                uncertainty=_optional_text(document.get("date_uncertainty")),
                safety_class=TemporalSafetyClass(_required_text(document, "temporal_safety_class")),
            ),
            warnings=tuple(sorted(set(warnings))),
        )

    def _snippet(self, document: dict[str, object]) -> EvidenceSnippet | None:
        """Enforce source policy and construct a bounded, attributed excerpt."""
        source_code = _required_text(document, "source_code")
        source_url = _required_text(document, "content_url")
        evaluation = self._policy.evaluate(
            source_code=source_code,
            action="display_snippet",
            url=source_url,
            content_class="normalized_text",
            attributed=True,
        )
        if evaluation.outcome.value != "allow":
            return None
        text = _required_text(document, "text")
        bounded = text if len(text) <= 500 else f"{text[:497].rstrip()}…"
        return EvidenceSnippet(
            evidence_unit_id=_required_text(document, "id"),
            normalized_document_id=_required_text(document, "normalized_document_id"),
            acquired_asset_id=_required_text(document, "acquired_asset_id"),
            text=bounded,
            access_mode="attributed_excerpt",
            section_path=_string_tuple(document.get("section_path")),
            locator=_required_text(document, "locator"),
            page_start=_optional_int(document.get("page_start")),
            page_end=_optional_int(document.get("page_end")),
            content_sha256=_required_text(document, "content_sha256"),
            text_sha256=_required_text(document, "text_sha256"),
            retrieved_at=_required_datetime(document, "retrieved_at"),
            parser_name=_required_text(document, "parser_name"),
            parser_version=_required_text(document, "parser_version"),
            extraction_schema_version=_required_text(document, "extraction_schema_version"),
            warnings=_string_tuple(document.get("quality_warnings")),
        )


def _collapse(candidates: dict[str, _Candidate], collapse_versions: bool) -> list[_Candidate]:
    """Sort versions, optionally retaining one highest-ranked version per work."""
    ordered = sorted(
        candidates.values(),
        key=lambda item: (-item.score, _required_text(item.document, "document_version_id")),
    )
    if not collapse_versions:
        return ordered
    selected: list[_Candidate] = []
    seen: set[str] = set()
    for item in ordered:
        work_id = _required_text(item.document, "work_id")
        if work_id not in seen:
            seen.add(work_id)
            selected.append(item)
    return selected


def _parsed_terms(query: str) -> tuple[str, ...]:
    """Expose quoted phrases first, followed by unique query terms."""
    phrases = [item.strip() for item in _PHRASE_PATTERN.findall(query) if item.strip()]
    terms = _TERM_PATTERN.findall(_PHRASE_PATTERN.sub(" ", query))
    return tuple(dict.fromkeys((*phrases, *terms)))


def _searchable_text(candidate: _Candidate) -> str:
    """Combine user-visible matched fields for explanations."""
    document = candidate.document
    evidence = candidate.best_evidence or {}
    return " ".join(
        (
            str(document.get("title") or ""),
            " ".join(_string_tuple(document.get("authors"))),
            str(document.get("abstract") or ""),
            str(evidence.get("text") or ""),
        )
    )


def _has_structured_filters(filters: SearchFilters) -> bool:
    """Return whether a structured channel has any selection predicate."""
    return bool(filters.source_codes or filters.published_from or filters.published_to)


def _is_suppressed(document: dict[str, object], suppressions: frozenset[tuple[str, str]]) -> bool:
    """Apply current tombstones even when the active index predates them."""
    source_code = document.get("source_code")
    source_identifier = document.get("source_identifier")
    if (
        isinstance(source_code, str)
        and isinstance(source_identifier, str)
        and ("source_record", f"{source_code}:{source_identifier}") in suppressions
    ):
        return True
    for key in ("source_url", "content_url"):
        value = document.get(key)
        if isinstance(value, str) and ("url", value) in suppressions:
            return True
    asset_ids = document.get("asset_ids")
    if isinstance(asset_ids, list) and any(("asset", str(value)) in suppressions for value in asset_ids):
        return True
    asset_id = document.get("acquired_asset_id")
    return isinstance(asset_id, str) and ("asset", asset_id) in suppressions


def _request_hash(request: SearchRequest) -> str:
    """Bind a cursor to immutable search semantics, excluding pagination flags."""
    return record_sha256(
        {
            "query": request.query,
            "modes": request.modes,
            "filters": request.filters,
            "historical_cutoff": request.historical_cutoff,
            "collapse_versions": request.collapse_versions,
        }
    )


def _encode_cursor(generation_id: str, request_hash: str, offset: int) -> str:
    """Create an opaque generation-bound pagination cursor."""
    value = json.dumps(
        {"generation": generation_id, "request": request_hash, "offset": offset},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str | None, generation_id: str, request_hash: str) -> int:
    """Validate and decode a cursor without accepting cross-query replay."""
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        offset = int(payload["offset"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ConflictError("search cursor is invalid") from error
    if payload.get("generation") != generation_id or payload.get("request") != request_hash:
        raise ConflictError("search cursor does not match the active generation and query")
    if not 0 <= offset <= _MAX_CANDIDATES:
        raise ConflictError("search cursor offset is outside the supported window")
    return offset


def _object(value: object) -> dict[str, object]:
    """Serialize one domain dataclass to a JSON-compatible object."""
    payload = to_json_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - callers pass dataclasses
        raise TypeError("expected serialized object")
    return cast(dict[str, object], payload)


def _required_text(document: dict[str, object], key: str) -> str:
    """Read a required index string as a provenance gate."""
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConflictError("search index document lacks required provenance", context={"field": key})
    return value


def _optional_text(value: object) -> str | None:
    """Read an optional index string."""
    return value if isinstance(value, str) else None


def _required_int(document: dict[str, object], key: str) -> int:
    """Read a required integer index field."""
    value = document.get(key)
    if not isinstance(value, int):
        raise ConflictError("search index document lacks required provenance", context={"field": key})
    return value


def _optional_int(value: object) -> int | None:
    """Read an optional integer index field."""
    return value if isinstance(value, int) else None


def _string_tuple(value: object) -> tuple[str, ...]:
    """Normalize one JSON string array."""
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()


def _optional_date(value: object) -> date | None:
    """Parse one optional index date."""
    return date.fromisoformat(value) if isinstance(value, str) else None


def _required_datetime(document: dict[str, object], key: str) -> datetime:
    """Parse one required UTC index timestamp."""
    value = _required_text(document, key)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ConflictError("search index timestamp is not UTC", context={"field": key})
    return parsed
