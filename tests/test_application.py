"""Application-service and reproducible export tests."""

import json
from datetime import UTC, datetime

import pytest

from economic_evidence_graph.application import EvidencePackExporter, ExportRequest, JobService
from economic_evidence_graph.domain import JobState
from economic_evidence_graph.exceptions import ConflictError, EconomicEvidenceGraphError
from economic_evidence_graph.providers import (
    DeterministicIdFactory,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryRepository,
)


@pytest.mark.parametrize(
    ("work_ids", "format"),
    [
        ((), "jsonl"),
        ((" ",), "jsonl"),
        (("wrk_1", "wrk_1"), "jsonl"),
        (("wrk_1",), "csv"),
    ],
)
def test_export_request_rejects_ambiguous_scope(work_ids: tuple[str, ...], format: str) -> None:
    """Export scope is bounded and deterministic before repository access."""
    with pytest.raises(EconomicEvidenceGraphError):
        ExportRequest(work_ids=work_ids, format=format)


def test_exporter_creates_checksum_addressed_evidence_pack(memory_system: object) -> None:
    """An accepted fixture exports into typed JSON Lines files and a sealed manifest."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    container = memory_system.container  # type: ignore[attr-defined]
    exporter = container.jobs.exporter
    result = exporter.export(ExportRequest(work_ids=(bundle.work.id,), snapshot_id=bundle.snapshot.id))

    manifest = json.loads(container.artifacts.get_bytes(result.manifest))
    assert manifest["manifest_id"] == result.manifest_id
    assert manifest["snapshot_id"] == bundle.snapshot.id
    assert manifest["work_ids"] == [bundle.work.id]
    assert [item["name"] for item in manifest["files"]] == [
        "works.jsonl",
        "document_versions.jsonl",
        "manifestations.jsonl",
        "evidence_units.jsonl",
        "evidence_records.jsonl",
    ]
    assert len(result.files) == 5
    assert json.loads(container.artifacts.get_bytes(result.files[0]))["id"] == bundle.work.id


def test_exporter_rejects_wrong_or_mixed_snapshots(memory_system: object) -> None:
    """A manifest cannot claim a snapshot that did not select the exported work."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    exporter = memory_system.container.jobs.exporter  # type: ignore[attr-defined]
    with pytest.raises(ConflictError):
        exporter.export(ExportRequest(work_ids=(bundle.work.id,), snapshot_id="snp_other"))


def test_job_service_is_idempotent_and_records_export_failure(memory_system: object) -> None:
    """Repeated client operations return one job and expected failures remain inspectable."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    jobs = memory_system.container.jobs  # type: ignore[attr-defined]
    request = ExportRequest(work_ids=(bundle.work.id,), snapshot_id=bundle.snapshot.id)
    first = jobs.submit_export(request=request, idempotency_key="export-one")
    second = jobs.submit_export(request=request, idempotency_key="export-one")
    failed = jobs.submit_export(
        request=ExportRequest(work_ids=("wrk_missing",)),
        idempotency_key="export-missing",
    )

    assert first is second
    assert first.state is JobState.SUCCEEDED
    assert first.output is not None
    assert first.output["file_count"] == 5
    assert failed.state is JobState.FAILED
    assert failed.error_code == "resource_not_found"
    assert jobs.get(first.id) == first


def test_job_service_failure_uses_expected_application_errors_only() -> None:
    """The failure path persists domain errors with stable codes."""
    repository = InMemoryRepository()
    artifacts = InMemoryArtifactStore()
    clock = FrozenClock(datetime(2026, 8, 4, tzinfo=UTC))
    exporter = EvidencePackExporter(catalog=repository, artifacts=artifacts, clock=clock)
    jobs = JobService(
        jobs=repository,
        exporter=exporter,
        clock=clock,
        ids=DeterministicIdFactory("failures"),
    )
    failed = jobs.submit_export(
        request=ExportRequest(work_ids=("wrk_missing",)),
        idempotency_key="missing",
    )
    assert failed.state is JobState.FAILED
