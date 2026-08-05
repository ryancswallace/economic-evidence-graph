"""In-memory and OpenSearch implementations of the search-index boundary."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, cast

import httpx

from economic_evidence_graph.domain import IndexGeneration, SearchEvidenceDocument, SearchWorkDocument
from economic_evidence_graph.exceptions import SearchBackendError
from economic_evidence_graph.ports import SearchIndexFilters, SearchIndexHit
from economic_evidence_graph.serialization import to_json_value

WORK_ALIAS = "eeg-work-current"
EVIDENCE_ALIAS = "eeg-evidence-current"
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class InMemorySearchIndex:
    """Deterministic provider used for contract tests and isolated development."""

    def __init__(self) -> None:
        self._work: dict[str, dict[str, object]] = {}
        self._evidence: dict[str, dict[str, object]] = {}
        self.active_generation: IndexGeneration | None = None

    def replace_generation(
        self,
        generation: IndexGeneration,
        work_documents: tuple[SearchWorkDocument, ...],
        evidence_documents: tuple[SearchEvidenceDocument, ...],
        *,
        embedding_dimension: int,
    ) -> None:
        """Atomically replace the in-memory active generation."""
        _validate_dimensions((*work_documents, *evidence_documents), embedding_dimension)
        self._work = {item.id: _document(item) for item in work_documents}
        self._evidence = {item.id: _document(item) for item in evidence_documents}
        self.active_generation = generation

    def search(
        self,
        *,
        index_kind: str,
        channel: str,
        query: str,
        query_vector: tuple[float, ...] | None,
        filters: SearchIndexFilters,
        limit: int,
    ) -> tuple[SearchIndexHit, ...]:
        """Search locally while applying filters before scoring."""
        documents = self._documents(index_kind)
        eligible = (item for item in documents.values() if _matches_filters(item, filters))
        scored: list[tuple[float, str, dict[str, object]]] = []
        for item in eligible:
            if channel == "semantic":
                if query_vector is None:
                    raise SearchBackendError("semantic search requires a query vector")
                score = _cosine(query_vector, _float_tuple(item.get("embedding")))
            elif channel == "structured":
                score = 1.0
            elif channel == "lexical":
                score = _lexical_score(query, item, index_kind)
            else:
                raise SearchBackendError(f"unsupported search channel: {channel}")
            if score > 0:
                scored.append((score, str(item["id"]), item))
        scored.sort(key=lambda value: (-value[0], value[1]))
        return tuple(
            SearchIndexHit(document=item, score=score, rank=rank)
            for rank, (score, _, item) in enumerate(scored[:limit], start=1)
        )

    def get_evidence(self, evidence_unit_id: str) -> dict[str, object] | None:
        """Return one evidence projection from the active generation."""
        return self._evidence.get(evidence_unit_id)

    def get_work_versions(self, work_id: str, *, limit: int) -> tuple[dict[str, object], ...]:
        """Return stable, bounded version projections for one work."""
        return tuple(
            sorted(
                (item for item in self._work.values() if item.get("work_id") == work_id),
                key=lambda item: str(item["document_version_id"]),
            )[:limit]
        )

    def list_version_evidence(
        self,
        document_version_id: str,
        *,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        """Return stable, bounded evidence projections for one version."""
        return tuple(
            sorted(
                (item for item in self._evidence.values() if item.get("document_version_id") == document_version_id),
                key=lambda item: (str(item.get("locator")), str(item["id"])),
            )[:limit]
        )

    def check(self) -> None:
        """The in-memory provider is always available."""

    def close(self) -> None:
        """The in-memory provider owns no external resources."""

    def _documents(self, index_kind: str) -> dict[str, dict[str, object]]:
        """Select an active document collection."""
        if index_kind == "work":
            return self._work
        if index_kind == "evidence":
            return self._evidence
        raise SearchBackendError(f"unsupported search index kind: {index_kind}")


class OpenSearchIndex:
    """Direct OpenSearch adapter with immutable generations and atomic aliases."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 30,
        bulk_batch_size: int = 500,
        client: httpx.Client | None = None,
    ) -> None:
        if not 1 <= bulk_batch_size <= 5_000:
            raise ValueError("OpenSearch bulk batch size must be between 1 and 5,000")
        self._client = client or httpx.Client(base_url=url.rstrip("/"), timeout=timeout)
        self._owns_client = client is None
        self._bulk_batch_size = bulk_batch_size

    def replace_generation(
        self,
        generation: IndexGeneration,
        work_documents: tuple[SearchWorkDocument, ...],
        evidence_documents: tuple[SearchEvidenceDocument, ...],
        *,
        embedding_dimension: int,
    ) -> None:
        """Build and validate both indexes, then switch both aliases at once."""
        _validate_dimensions((*work_documents, *evidence_documents), embedding_dimension)
        self._ensure_index(
            generation.work_index_name,
            _work_mapping(embedding_dimension),
            (_document(item) for item in work_documents),
            len(work_documents),
        )
        self._ensure_index(
            generation.evidence_index_name,
            _evidence_mapping(embedding_dimension),
            (_document(item) for item in evidence_documents),
            len(evidence_documents),
        )
        actions = self._alias_removals(WORK_ALIAS) + self._alias_removals(EVIDENCE_ALIAS)
        actions.extend(
            (
                {"add": {"index": generation.work_index_name, "alias": WORK_ALIAS}},
                {"add": {"index": generation.evidence_index_name, "alias": EVIDENCE_ALIAS}},
            )
        )
        self._request("POST", "/_aliases", json_body={"actions": actions})

    def search(
        self,
        *,
        index_kind: str,
        channel: str,
        query: str,
        query_vector: tuple[float, ...] | None,
        filters: SearchIndexFilters,
        limit: int,
    ) -> tuple[SearchIndexHit, ...]:
        """Run a channel with filters embedded in the provider query."""
        alias = _alias(index_kind)
        filter_clauses = _filter_clauses(filters)
        if channel == "semantic":
            if query_vector is None:
                raise SearchBackendError("semantic search requires a query vector")
            knn: dict[str, object] = {"vector": list(query_vector), "k": limit}
            if filter_clauses:
                knn["filter"] = {"bool": {"filter": filter_clauses}}
            query_body: dict[str, object] = {"knn": {"embedding": knn}}
        elif channel == "structured":
            query_body = {"bool": {"filter": filter_clauses or [{"match_all": {}}]}}
        elif channel == "lexical":
            fields = (
                ["title^5", "authors^2", "abstract^3", "identifiers^4"]
                if index_kind == "work"
                else ["title^3", "authors", "section_path^2", "text^4"]
            )
            query_body = {
                "bool": {
                    "must": [{"multi_match": {"query": query, "fields": fields, "type": "best_fields"}}],
                    "filter": filter_clauses,
                }
            }
        else:
            raise SearchBackendError(f"unsupported search channel: {channel}")
        payload = self._request(
            "POST",
            f"/{alias}/_search",
            json_body={"size": limit, "track_total_hits": False, "query": query_body},
        )
        hits = cast(dict[str, Any], payload.get("hits", {})).get("hits", [])
        if not isinstance(hits, list):
            raise SearchBackendError("OpenSearch returned malformed hits")
        return tuple(
            SearchIndexHit(
                document=cast(dict[str, object], hit["_source"]),
                score=float(hit.get("_score") or 0.0),
                rank=rank,
            )
            for rank, hit in enumerate(cast(list[dict[str, Any]], hits), start=1)
        )

    def get_evidence(self, evidence_unit_id: str) -> dict[str, object] | None:
        """Return one evidence projection by exact identifier."""
        response = self._client.get(f"/{EVIDENCE_ALIAS}/_doc/{evidence_unit_id}")
        if response.status_code == 404:
            return None
        payload = self._response(response)
        return cast(dict[str, object], payload.get("_source"))

    def get_work_versions(self, work_id: str, *, limit: int) -> tuple[dict[str, object], ...]:
        """Return stable, bounded version projections for one exact work."""
        return self._exact_documents(
            WORK_ALIAS,
            field="work_id",
            value=work_id,
            limit=limit,
            sort_field="document_version_id",
        )

    def list_version_evidence(
        self,
        document_version_id: str,
        *,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        """Return stable, bounded evidence projections for one exact version."""
        return self._exact_documents(
            EVIDENCE_ALIAS,
            field="document_version_id",
            value=document_version_id,
            limit=limit,
            sort_field="id",
        )

    def check(self) -> None:
        """Require a non-red OpenSearch cluster."""
        payload = self._request("GET", "/_cluster/health")
        if payload.get("status") == "red":
            raise SearchBackendError("OpenSearch cluster status is red")

    def close(self) -> None:
        """Release the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def _ensure_index(
        self,
        name: str,
        mapping: dict[str, object],
        documents: Iterable[dict[str, object]],
        expected_count: int,
    ) -> None:
        """Create an absent immutable index or validate its existing contents."""
        response = self._client.head(f"/{name}")
        if response.status_code == 404:
            self._request("PUT", f"/{name}", json_body=mapping)
            self._bulk(name, documents)
            self._request("POST", f"/{name}/_refresh")
        elif response.is_error:
            self._response(response)
        count = self._request("GET", f"/{name}/_count").get("count")
        if count != expected_count:
            raise SearchBackendError(
                "immutable index count validation failed",
                context={"index": name, "expected": str(expected_count), "actual": str(count)},
            )

    def _exact_documents(
        self,
        alias: str,
        *,
        field: str,
        value: str,
        limit: int,
        sort_field: str,
    ) -> tuple[dict[str, object], ...]:
        """Read exact keyword matches without exposing provider query details."""
        payload = self._request(
            "POST",
            f"/{alias}/_search",
            json_body={
                "size": limit,
                "track_total_hits": False,
                "query": {"term": {field: value}},
                "sort": [{sort_field: "asc"}, {"_id": "asc"}],
            },
        )
        hits = cast(dict[str, Any], payload.get("hits", {})).get("hits", [])
        if not isinstance(hits, list):
            raise SearchBackendError("OpenSearch returned malformed exact-match hits")
        return tuple(cast(dict[str, object], hit["_source"]) for hit in cast(list[dict[str, Any]], hits))

    def _bulk(self, index_name: str, documents: Iterable[dict[str, object]]) -> None:
        """Upload deterministic newline-delimited bulk operations."""
        lines: list[str] = []
        for document in documents:
            lines.append(json.dumps({"index": {"_index": index_name, "_id": document["id"]}}))
            lines.append(json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
            if len(lines) == self._bulk_batch_size * 2:
                self._post_bulk(lines)
                lines = []
        if lines:
            self._post_bulk(lines)

    def _post_bulk(self, lines: list[str]) -> None:
        """Post one bounded NDJSON batch and validate every item."""
        response = self._client.post(
            "/_bulk",
            content=("\n".join(lines) + "\n").encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )
        payload = self._response(response)
        if payload.get("errors") is True:
            raise SearchBackendError("OpenSearch bulk indexing reported item errors")

    def _alias_removals(self, alias: str) -> list[dict[str, object]]:
        """Resolve current targets for an atomic, explicit alias switch."""
        response = self._client.get(f"/_alias/{alias}")
        if response.status_code == 404:
            return []
        payload = self._response(response)
        return [{"remove": {"index": name, "alias": alias}} for name in sorted(payload)]

    def _request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, Any]:
        """Issue one JSON request and normalize transport failures."""
        try:
            response = self._client.request(method, path, json=json_body)
        except httpx.HTTPError as error:
            raise SearchBackendError("OpenSearch request failed") from error
        return self._response(response)

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        """Validate an OpenSearch JSON response."""
        if response.is_error:
            raise SearchBackendError(
                "OpenSearch rejected a request",
                context={"status": str(response.status_code), "path": response.request.url.path},
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise SearchBackendError("OpenSearch returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise SearchBackendError("OpenSearch returned a non-object response")
        return cast(dict[str, Any], payload)


def _document(item: object) -> dict[str, object]:
    """Serialize one projection as a strict JSON object."""
    value = to_json_value(item)
    if not isinstance(value, dict):  # pragma: no cover - all accepted projections are dataclasses
        raise TypeError("search document must serialize as an object")
    return cast(dict[str, object], value)


def _validate_dimensions(documents: Iterable[SearchWorkDocument | SearchEvidenceDocument], expected: int) -> None:
    """Reject inconsistent embeddings before mutating an index."""
    for document in documents:
        if len(document.embedding) != expected:
            raise SearchBackendError("search projection embedding dimension is inconsistent")


def _work_mapping(dimension: int) -> dict[str, object]:
    """Return the strict work-index mapping."""
    return _mapping(
        dimension,
        {
            "abstract": {"type": "text"},
            "asset_ids": {"type": "keyword"},
            "identifiers": {"type": "keyword"},
            "normalized_document_ids": {"type": "keyword"},
            "rights_statuses": {"type": "keyword"},
        },
    )


def _evidence_mapping(dimension: int) -> dict[str, object]:
    """Return the strict evidence-index mapping."""
    return _mapping(
        dimension,
        {
            "acquired_asset_id": {"type": "keyword"},
            "content_sha256": {"type": "keyword"},
            "content_url": {"type": "keyword", "index": False},
            "extraction_schema_version": {"type": "keyword"},
            "locator": {"type": "keyword", "index": False},
            "normalized_document_id": {"type": "keyword"},
            "page_end": {"type": "integer"},
            "page_start": {"type": "integer"},
            "parser_name": {"type": "keyword"},
            "parser_version": {"type": "keyword"},
            "retrieved_at": {"type": "date"},
            "rights_status": {"type": "keyword"},
            "section_path": {"type": "text"},
            "text": {"type": "text"},
            "text_sha256": {"type": "keyword"},
        },
    )


def _mapping(dimension: int, extra: dict[str, object]) -> dict[str, object]:
    """Return common strict settings and fields."""
    properties: dict[str, object] = {
        "authors": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "date_basis": {"type": "keyword"},
        "date_evidence": {"type": "keyword", "index": False},
        "date_uncertainty": {"type": "keyword", "index": False},
        "document_version_id": {"type": "keyword"},
        "eligible_from": {"type": "date"},
        "embedding": {
            "type": "knn_vector",
            "dimension": dimension,
            "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
        },
        "id": {"type": "keyword"},
        "observed_at": {"type": "date"},
        "policy_version": {"type": "integer"},
        "published_at": {"type": "date"},
        "quality_warnings": {"type": "keyword"},
        "source_code": {"type": "keyword"},
        "source_identifier": {"type": "keyword"},
        "source_record_id": {"type": "keyword"},
        "source_url": {"type": "keyword", "index": False},
        "temporal_safety_class": {"type": "keyword"},
        "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "version_count": {"type": "integer"},
        "work_id": {"type": "keyword"},
    }
    properties.update(extra)
    return {"settings": {"index": {"knn": True}}, "mappings": {"dynamic": "strict", "properties": properties}}


def _alias(index_kind: str) -> str:
    """Map a provider-neutral kind to an active alias."""
    if index_kind == "work":
        return WORK_ALIAS
    if index_kind == "evidence":
        return EVIDENCE_ALIAS
    raise SearchBackendError(f"unsupported search index kind: {index_kind}")


def _filter_clauses(filters: SearchIndexFilters) -> list[dict[str, object]]:
    """Build filters applied before every ranking channel."""
    clauses: list[dict[str, object]] = []
    if filters.source_codes:
        clauses.append({"terms": {"source_code": list(filters.source_codes)}})
    if filters.published_from is not None or filters.published_to is not None:
        bounds: dict[str, object] = {}
        if filters.published_from is not None:
            bounds["gte"] = filters.published_from.isoformat()
        if filters.published_to is not None:
            bounds["lte"] = filters.published_to.isoformat()
        clauses.append({"range": {"published_at": bounds}})
    if filters.eligible_at is not None:
        clauses.append({"range": {"eligible_from": {"lte": filters.eligible_at.isoformat()}}})
    if filters.allowed_safety_classes:
        clauses.append({"terms": {"temporal_safety_class": list(filters.allowed_safety_classes)}})
    return clauses


def _matches_filters(document: dict[str, object], filters: SearchIndexFilters) -> bool:
    """Apply provider filters to one in-memory document before scoring."""
    if filters.source_codes and document.get("source_code") not in filters.source_codes:
        return False
    published = _date(document.get("published_at"))
    if filters.published_from is not None and (published is None or published < filters.published_from):
        return False
    if filters.published_to is not None and (published is None or published > filters.published_to):
        return False
    eligible = _datetime(document.get("eligible_from"))
    if filters.eligible_at is not None and (eligible is None or eligible > filters.eligible_at):
        return False
    return not filters.allowed_safety_classes or document.get("temporal_safety_class") in filters.allowed_safety_classes


def _lexical_score(query: str, document: dict[str, object], index_kind: str) -> float:
    """Return a weighted token-overlap score for the memory contract."""
    tokens = _TOKEN_PATTERN.findall(query.casefold())
    if not tokens:
        return 0.0
    fields = (
        (("title", 5.0), ("authors", 2.0), ("abstract", 3.0), ("identifiers", 4.0))
        if index_kind == "work"
        else (("title", 3.0), ("authors", 1.0), ("section_path", 2.0), ("text", 4.0))
    )
    score = 0.0
    for field, weight in fields:
        value = document.get(field)
        haystack = " ".join(str(item) for item in value) if isinstance(value, list | tuple) else str(value or "")
        lowered = haystack.casefold()
        score += weight * sum(lowered.count(token) for token in tokens)
    return score


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Compute a bounded nonnegative cosine score."""
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    return 0.0 if denominator == 0 else max(0.0, sum(a * b for a, b in zip(left, right, strict=True)) / denominator)


def _float_tuple(value: object) -> tuple[float, ...]:
    """Normalize one JSON vector."""
    if not isinstance(value, list) or any(not isinstance(item, int | float) for item in value):
        return ()
    return tuple(float(item) for item in cast(list[int | float], value))


def _date(value: object) -> date | None:
    """Parse one optional JSON date."""
    return date.fromisoformat(value) if isinstance(value, str) else None


def _datetime(value: object) -> datetime | None:
    """Parse one optional JSON datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
