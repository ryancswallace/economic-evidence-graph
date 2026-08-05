"""HTTP contract and Step 1 end-to-end acceptance tests."""

import json
from asyncio import run
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI

from economic_evidence_graph.api import create_app
from economic_evidence_graph.domain import Job


class ApiClient:
    """Small synchronous facade over HTTPX's ASGI transport."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        """Issue a GET request against the in-process ASGI application."""
        return run(self._request("GET", path, headers=headers))

    def post(self, path: str, *, json: object) -> httpx.Response:
        """Issue a JSON POST request against the in-process ASGI application."""
        return run(self._request("POST", path, json=json))

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Use the public ASGI interface without a framework-specific test facade."""
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)


def test_health_and_openapi_contract(memory_system: object) -> None:
    """The API exposes versioned operations and checks both canonical providers."""
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    assert client.get("/health/live").json() == {"status": "ok", "profile": "local"}
    assert client.get("/health/ready").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/v1/works/{work_id}" in schema["paths"]
    assert "/v1/exports" in schema["paths"]
    assert schema["components"]["schemas"]["WorkResponse"]["additionalProperties"] is False


def test_work_response_has_exact_evidence_and_lineage(memory_system: object) -> None:
    """A work API response includes no unanchored analytical value."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    response = client.get(f"/v1/works/{bundle.work.id}", headers={"X-Request-ID": "acceptance-request"})
    body = response.json()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "acceptance-request"
    assert body["work"]["id"] == bundle.work.id
    assert body["provenance"]["snapshot_id"] == bundle.snapshot.id
    assert body["evidence_records"][0]["fields"][2]["normalized_value"] == -1.5
    assert body["evidence_records"][0]["fields"][2]["anchors"][0]["evidence_unit_id"]


def test_fixture_flows_through_export_job_and_manifest_api(memory_system: object) -> None:
    """Step 1 exit gate: one fixture survives API, job, export, and checksum lineage."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    request = {
        "work_ids": [bundle.work.id],
        "snapshot_id": bundle.snapshot.id,
        "format": "jsonl",
        "idempotency_key": "api-platform-acceptance",
    }

    created = client.post("/v1/exports", json=request)
    repeated = client.post("/v1/exports", json=request)
    job = created.json()
    manifest_response = client.get(f"/v1/exports/{job['id']}/manifest")
    manifest = manifest_response.json()

    assert created.status_code == 202
    assert created.json() == repeated.json()
    assert job["state"] == "succeeded"
    assert client.get(f"/v1/jobs/{job['id']}").json() == job
    assert manifest_response.status_code == 200
    assert manifest_response.headers["etag"] == f'"sha256:{job["output"]["manifest_sha256"]}"'
    assert manifest["snapshot_id"] == bundle.snapshot.id
    assert manifest["work_ids"] == [bundle.work.id]
    assert len(manifest["files"]) == 5
    assert json.dumps(manifest, sort_keys=True)


def test_problem_details_cover_not_found_conflict_and_validation(memory_system: object) -> None:
    """Expected API failures have one stable machine-readable contract."""
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    missing = client.get("/v1/works/wrk_missing")
    invalid = client.post("/v1/exports", json={"work_ids": []})
    pending = Job.pending(
        id="job_pending",
        kind="evidence_pack_export",
        idempotency_key="pending",
        input={},
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    memory_system.repository.add(pending)  # type: ignore[attr-defined]
    conflict = client.get("/v1/exports/job_pending/manifest")

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "resource_not_found"
    assert missing.json()["context"] == {"work_id": "wrk_missing"}
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "request_validation_error"
    assert invalid.json()["errors"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "immutable_record_conflict"


def test_oversized_request_id_is_replaced(memory_system: object) -> None:
    """Caller request IDs are bounded before entering logs or responses."""
    client = ApiClient(create_app(memory_system.container))  # type: ignore[attr-defined]
    response = client.get("/health/live", headers={"X-Request-ID": "x" * 129})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "x" * 129
