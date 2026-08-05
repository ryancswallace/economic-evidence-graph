"""Domain identifier, lineage, state-machine, and serialization tests."""

from dataclasses import replace
from datetime import UTC, datetime
from math import nan
from types import MappingProxyType

import pytest

from economic_evidence_graph.domain import (
    AccessStatus,
    EvidenceState,
    Job,
    JobState,
    TemporalSafetyClass,
    content_id,
    stable_id,
)
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError, InvalidStateTransitionError
from economic_evidence_graph.serialization import canonical_json_bytes, record_sha256, to_json_value


def test_stable_identifiers_are_deterministic_and_namespaced() -> None:
    """Canonical components generate stable, kind-specific IDs."""
    first = stable_id("wrk", "RePEc:test:paper:1", "économie")
    assert first == stable_id("wrk", "RePEc:test:paper:1", "économie")
    assert first != stable_id("ver", "RePEc:test:paper:1", "économie")
    assert first.startswith("wrk_")
    assert content_id("raw", "a" * 64) == content_id("raw", "a" * 64)


@pytest.mark.parametrize(
    ("prefix", "components"),
    [
        ("X", ("value",)),
        ("way_too_long_prefix", ("value",)),
        ("ok", ()),
        ("ok", (" ",)),
    ],
)
def test_stable_identifiers_reject_ambiguous_input(prefix: str, components: tuple[str, ...]) -> None:
    """Malformed identifier input cannot silently enter persistent state."""
    with pytest.raises(EconomicEvidenceGraphError):
        stable_id(prefix, *components)


def test_content_identifier_requires_a_sha256() -> None:
    """Content IDs reject noncanonical digests."""
    with pytest.raises(EconomicEvidenceGraphError, match="SHA-256"):
        content_id("raw", "ABC")


def test_fixture_lineage_is_complete_and_stable(memory_system: object) -> None:
    """The synthetic acceptance fixture validates all lineage edges."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    bundle.validate_lineage()
    assert bundle.document_versions[0].work_id == bundle.work.id
    assert bundle.evidence_records[0].fields[2].anchors[0].evidence_unit_id == bundle.evidence_units[0].id
    assert bundle.snapshot.members[0].extraction_artifact_id == bundle.extraction_artifacts[0].id


def test_bundle_rejects_an_unresolved_lineage_edge(memory_system: object) -> None:
    """An evidence unit cannot refer to an extraction outside its bundle."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    broken_unit = replace(bundle.evidence_units[0], extraction_artifact_id="ext_missing")
    with pytest.raises(EconomicEvidenceGraphError, match="unresolved lineage"):
        replace(bundle, evidence_units=(broken_unit,))


def test_bundle_rejects_cross_version_evidence_anchors(memory_system: object) -> None:
    """A record cannot borrow an evidence unit from a different document version."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    other_version = replace(bundle.document_versions[0], id="ver_other")
    broken_record = replace(bundle.evidence_records[0], document_version_id=other_version.id)
    with pytest.raises(EconomicEvidenceGraphError, match="unresolved lineage"):
        replace(
            bundle,
            document_versions=(*bundle.document_versions, other_version),
            evidence_records=(broken_record,),
        )


def test_job_state_machine_preserves_immutable_input() -> None:
    """Jobs follow pending-running-terminal transitions with immutable mappings."""
    now = datetime(2026, 8, 4, tzinfo=UTC)
    pending = Job.pending(id="job_1", kind="test_job", idempotency_key="key", input={"value": 1}, created_at=now)
    running = pending.start(now)
    succeeded = running.succeed(now, {"result": "ok"})

    assert pending.state is JobState.PENDING
    assert running.state is JobState.RUNNING
    assert succeeded.state is JobState.SUCCEEDED
    assert succeeded.output == {"result": "ok"}
    with pytest.raises(TypeError):
        pending.input["value"] = 2  # type: ignore[index]


def test_job_can_fail_and_rejects_invalid_transitions() -> None:
    """Failure state is structured and terminal states cannot be restarted."""
    now = datetime(2026, 8, 4, tzinfo=UTC)
    pending = Job.pending(id="job_1", kind="test_job", idempotency_key="key", input={}, created_at=now)
    failed = pending.start(now).fail(now, code="fixture_failure", message="expected")
    assert failed.state is JobState.FAILED
    assert failed.error_code == "fixture_failure"
    with pytest.raises(InvalidStateTransitionError):
        pending.succeed(now, {})
    with pytest.raises(InvalidStateTransitionError):
        pending.fail(now, code="bad", message="bad")
    with pytest.raises(InvalidStateTransitionError):
        failed.start(now)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bundle: replace(bundle.sources[0], code="Bad Code"),
        lambda bundle: replace(bundle.source_policies[0], version=0),
        lambda bundle: replace(bundle.retrieval_events[0], http_status=99),
        lambda bundle: replace(bundle.retrieval_events[0], content_sha256="bad"),
        lambda bundle: replace(bundle.raw_assets[0], byte_length=-1),
        lambda bundle: replace(bundle.raw_assets[0], storage_uri=None),
        lambda bundle: replace(bundle.work, authors=()),
        lambda bundle: replace(bundle.document_versions[0], version_label=" "),
        lambda bundle: replace(bundle.extraction_artifacts[0], parser_name="Bad Parser"),
        lambda bundle: replace(bundle.evidence_units[0], ordinal=-1),
        lambda bundle: replace(bundle.evidence_units[0], page_start=0),
        lambda bundle: replace(bundle.evidence_units[0], section_path=()),
        lambda bundle: replace(bundle.evidence_records[0].fields[0], confidence=1.1),
        lambda bundle: replace(bundle.evidence_records[0].fields[0], anchors=()),
        lambda bundle: replace(bundle.evidence_records[0], fields=()),
        lambda bundle: replace(bundle.snapshot, manifest_sha256="invalid"),
    ],
)
def test_domain_objects_reject_invalid_state(memory_system: object, mutation: object) -> None:
    """Core model invariants fail at construction rather than at delivery time."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    with pytest.raises(EconomicEvidenceGraphError):
        mutation(bundle)  # type: ignore[operator]


def test_timestamps_must_be_aware_utc(memory_system: object) -> None:
    """Naive and non-UTC timestamps cannot become audit timestamps."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    with pytest.raises(EconomicEvidenceGraphError, match="UTC"):
        replace(bundle.retrieval_events[0], retrieved_at=datetime(2026, 8, 4))


def test_strict_json_serialization_handles_domain_values(memory_system: object) -> None:
    """Domain values serialize canonically without Python-only metadata."""
    bundle = memory_system.bundle  # type: ignore[attr-defined]
    value = {
        "state": EvidenceState.OBSERVED,
        "safety": TemporalSafetyClass.SOURCE_FACT,
        "access": AccessStatus.STORED_OPEN,
        "mapping": MappingProxyType({"number": 1}),
        "work": bundle.work,
        "items": (True, None),
    }
    encoded = canonical_json_bytes(value)
    assert encoded == canonical_json_bytes(value)
    assert b'"state":"observed"' in encoded
    assert record_sha256(value) == record_sha256(value)
    assert to_json_value(bundle.work)["id"] == bundle.work.id  # type: ignore[index]


def test_strict_json_serialization_rejects_unknown_and_nan_values() -> None:
    """Exports cannot contain Python-only objects or nonstandard JSON numbers."""
    with pytest.raises(TypeError, match="unsupported JSON value"):
        to_json_value({1, 2})
    with pytest.raises(ValueError, match="Out of range"):
        canonical_json_bytes({"invalid": nan})
