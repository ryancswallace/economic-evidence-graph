"""Artifact, runtime, and repository provider contract tests."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from economic_evidence_graph.domain import Job
from economic_evidence_graph.exceptions import ConflictError, NotFoundError
from economic_evidence_graph.fixtures import build_platform_fixture
from economic_evidence_graph.ports import ArtifactRef
from economic_evidence_graph.providers import (
    DeterministicIdFactory,
    FileArtifactStore,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryRepository,
    RandomIdFactory,
    SystemClock,
)
from economic_evidence_graph.providers.postgres import SqlRepository
from economic_evidence_graph.providers.postgres.schema import metadata


@pytest.mark.parametrize("store_kind", ["memory", "file"])
def test_artifact_store_is_content_addressed_and_idempotent(store_kind: str, tmp_path: Path) -> None:
    """Both local artifact providers satisfy the same identity contract."""
    store = InMemoryArtifactStore() if store_kind == "memory" else FileArtifactStore(tmp_path / "artifacts")
    content = b"economic evidence"

    first = store.put_bytes(content, media_type="text/plain")
    second = store.put_bytes(content, media_type="text/plain")

    assert first == second
    assert store.get_bytes(first) == content
    assert store.delete(first)
    assert not store.delete(first)
    store.check()


@pytest.mark.parametrize("store_kind", ["memory", "file"])
def test_artifact_store_rejects_missing_or_mismatched_content(store_kind: str, tmp_path: Path) -> None:
    """Artifact delivery verifies both digest and byte length."""
    store = InMemoryArtifactStore() if store_kind == "memory" else FileArtifactStore(tmp_path / "artifacts")
    ref = store.put_bytes(b"expected", media_type="text/plain")
    mismatched = ArtifactRef(uri=ref.uri, sha256=ref.sha256, media_type=ref.media_type, byte_length=99)
    with pytest.raises(ConflictError):
        store.get_bytes(mismatched)
    missing = ArtifactRef(uri="artifact://sha256/" + "f" * 64, sha256="f" * 64, media_type="text/plain", byte_length=1)
    with pytest.raises(NotFoundError):
        store.get_bytes(missing)


def test_file_artifact_store_rejects_malformed_and_corrupted_records(tmp_path: Path) -> None:
    """Filesystem corruption cannot be returned as valid evidence."""
    root = tmp_path / "artifacts"
    store = FileArtifactStore(root)
    ref = store.put_bytes(b"expected", media_type="text/plain")
    stored = root / ref.sha256[:2] / ref.sha256[2:4] / ref.sha256
    stored.write_bytes(b"corrupt")
    with pytest.raises(ConflictError):
        store.get_bytes(ref)
    with pytest.raises(NotFoundError, match="malformed"):
        store.get_bytes(ArtifactRef(uri="bad", sha256="../bad", media_type="text/plain", byte_length=1))


def test_runtime_providers_return_utc_and_well_formed_ids() -> None:
    """Runtime ports provide test determinism without changing production contracts."""
    now = datetime(2026, 8, 4, tzinfo=UTC)
    assert FrozenClock(now).now() == now
    assert SystemClock().now().utcoffset() == UTC.utcoffset(now)
    assert RandomIdFactory().new("job").startswith("job_")
    ids = DeterministicIdFactory("seed")
    assert ids.new("job") != ids.new("job")
    assert DeterministicIdFactory("seed").new("job") == DeterministicIdFactory("seed").new("job")


def test_in_memory_catalog_is_idempotent_and_detects_conflicts() -> None:
    """The test provider enforces immutable bundle semantics."""
    repository = InMemoryRepository()
    bundle = build_platform_fixture(InMemoryArtifactStore())
    repository.put_bundle(bundle)
    repository.put_bundle(bundle)
    assert repository.get_bundle(bundle.work.id) == bundle
    changed = replace(bundle, work=replace(bundle.work, title="Changed title"))
    with pytest.raises(ConflictError):
        repository.put_bundle(changed)
    with pytest.raises(NotFoundError):
        repository.get_bundle("wrk_missing")
    repository.check()


def test_in_memory_job_repository_enforces_keys_and_presence() -> None:
    """Job IDs and idempotency keys remain unique and immutable."""
    repository = InMemoryRepository()
    now = datetime(2026, 8, 4, tzinfo=UTC)
    pending = Job.pending(id="job_1", kind="test_job", idempotency_key="key", input={}, created_at=now)
    repository.add(pending)
    assert repository.get("job_1") == pending
    assert repository.find_by_idempotency_key("key") == pending
    assert repository.find_by_idempotency_key("missing") is None
    with pytest.raises(ConflictError):
        repository.add(pending)
    with pytest.raises(ConflictError):
        repository.add(pending.start(now))
    with pytest.raises(NotFoundError):
        repository.get("job_missing")
    with pytest.raises(NotFoundError):
        repository.save(replace(pending, id="job_missing"))
    with pytest.raises(ConflictError):
        repository.save(replace(pending, idempotency_key="changed"))


def test_sql_catalog_round_trip_and_immutable_conflict() -> None:
    """The SQL adapter rehydrates the complete fixture without losing order or lineage."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlRepository(engine)
    bundle = build_platform_fixture(InMemoryArtifactStore())

    repository.put_bundle(bundle)
    repository.put_bundle(bundle)

    assert repository.get_bundle(bundle.work.id) == bundle
    repository.check()
    with pytest.raises(NotFoundError):
        repository.get_bundle("wrk_missing")
    with pytest.raises(ConflictError, match="immutable"):
        repository.put_bundle(replace(bundle, work=replace(bundle.work, title="Changed title")))
    repository.close()


def test_sql_job_repository_enforces_transitions_and_idempotency() -> None:
    """Job transitions are persisted with uniqueness and optimistic state checks."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlRepository(engine)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    pending = Job.pending(id="job_1", kind="test_job", idempotency_key="key", input={"one": 1}, created_at=now)
    repository.add(pending)
    assert repository.get("job_1") == pending
    assert repository.find_by_idempotency_key("missing") is None
    assert repository.find_by_idempotency_key("key") == pending

    running = pending.start(now)
    repository.save(running)
    repository.save(running)
    succeeded = running.succeed(now, {"result": "ok"})
    repository.save(succeeded)
    assert repository.get("job_1") == succeeded

    with pytest.raises(ConflictError):
        repository.add(pending)
    with pytest.raises(ConflictError, match="pending"):
        repository.add(running)
    with pytest.raises(ConflictError, match="valid next state"):
        repository.save(replace(succeeded, state=running.state))
    with pytest.raises(NotFoundError):
        repository.get("job_missing")
    with pytest.raises(NotFoundError):
        repository.save(replace(pending, id="job_missing"))
    repository.close()
