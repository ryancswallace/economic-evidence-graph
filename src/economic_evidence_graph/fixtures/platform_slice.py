"""Synthetic macro-policy fixture with complete deterministic lineage."""

import hashlib
from datetime import UTC, date, datetime

from economic_evidence_graph.domain import (
    AccessStatus,
    CorpusSnapshot,
    DocumentVersion,
    EvidenceAnchor,
    EvidenceBundle,
    EvidenceFieldRevision,
    EvidenceRecord,
    EvidenceState,
    EvidenceUnit,
    ExternalIdentifier,
    ExtractionArtifact,
    Manifestation,
    RawAsset,
    RetrievalEvent,
    SnapshotMember,
    Source,
    SourcePolicyVersion,
    TemporalSafetyClass,
    Work,
    WorkType,
    content_id,
    stable_id,
)
from economic_evidence_graph.ports import ArtifactStore
from economic_evidence_graph.serialization import record_sha256

FIXTURE_TEXT = """Synthetic fixture; it is not a real research paper and must not be cited.

Results
A 100-basis-point monetary policy tightening reduces synthetic bank lending by 1.5 percent after four quarters
(standard error 0.4 percentage point) in the baseline local-projection specification.

Limitations
The generated observations exist only to test provenance, versioning, APIs, and exports.
"""

_OBSERVED_AT = datetime(2019, 6, 3, 14, 0, tzinfo=UTC)


def build_platform_fixture(artifacts: ArtifactStore) -> EvidenceBundle:
    """Build and store the deterministic Step 1 acceptance fixture.

    Args:
        artifacts: Store receiving the synthetic raw source bytes.

    Returns:
        A complete evidence bundle representing one macro-policy working paper.
    """
    raw_bytes = FIXTURE_TEXT.encode("utf-8")
    raw_ref = artifacts.put_bytes(raw_bytes, media_type="text/plain")
    source = Source(
        id=stable_id("src", "platform_fixture"),
        code="platform_fixture",
        display_name="Synthetic Platform Acceptance Fixture",
        source_kind="synthetic_fixture",
    )
    policy = SourcePolicyVersion(
        id=stable_id("pol", source.id, "1"),
        source_id=source.id,
        version=1,
        effective_at=_OBSERVED_AT,
        review_status="approved_test_only",
    )
    retrieval = RetrievalEvent(
        id=stable_id("ret", source.id, raw_ref.sha256, _OBSERVED_AT.isoformat()),
        source_id=source.id,
        source_policy_version_id=policy.id,
        requested_url="https://example.invalid/eeg/synthetic-macro-policy-paper.txt",
        resolved_url="https://example.invalid/eeg/synthetic-macro-policy-paper.txt",
        retrieved_at=_OBSERVED_AT,
        http_status=200,
        media_type="text/plain",
        content_sha256=raw_ref.sha256,
        collector_version="fixture-1.0.0",
    )
    raw_asset = RawAsset(
        id=content_id("raw", raw_ref.sha256),
        retrieval_event_id=retrieval.id,
        sha256=raw_ref.sha256,
        media_type=raw_ref.media_type,
        byte_length=raw_ref.byte_length,
        storage_uri=raw_ref.uri,
        access_status=AccessStatus.STORED_OPEN,
    )
    identifier = ExternalIdentifier(
        id=stable_id("xid", "repec", "RePEc:eeg:test:0001"),
        scheme="repec",
        value="RePEc:eeg:test:0001",
    )
    work = Work(
        id=stable_id("wrk", source.code, identifier.value),
        title="Monetary Policy Pass-Through in a Synthetic Banking Panel",
        work_type=WorkType.WORKING_PAPER,
        authors=("A. Synthetic Economist", "B. Fixture Researcher"),
        identifiers=(identifier,),
    )
    version = DocumentVersion(
        id=stable_id("ver", work.id, raw_asset.sha256),
        work_id=work.id,
        raw_asset_id=raw_asset.id,
        version_label="synthetic working-paper version 1",
        asserted_at=date(2019, 6, 1),
        first_observed_at=_OBSERVED_AT,
        eligible_from=_OBSERVED_AT,
        temporal_safety_class=TemporalSafetyClass.SOURCE_FACT,
    )
    manifestation = Manifestation(
        id=stable_id("man", source.id, identifier.value, raw_asset.sha256),
        document_version_id=version.id,
        source_id=source.id,
        source_identifier=identifier.value,
        source_url=retrieval.resolved_url,
        access_status=AccessStatus.STORED_OPEN,
        license_uri="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    text_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    extraction = ExtractionArtifact(
        id=stable_id("ext", version.id, "fixture_parser", "1.0.0", text_sha256),
        document_version_id=version.id,
        raw_asset_id=raw_asset.id,
        parser_name="fixture_parser",
        parser_version="1.0.0",
        schema_version="eeg.normalized-document.v1",
        text_sha256=text_sha256,
        created_at=_OBSERVED_AT,
        temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
    )
    passage = (
        "A 100-basis-point monetary policy tightening reduces synthetic bank lending by 1.5 percent after four "
        "quarters (standard error 0.4 percentage point) in the baseline local-projection specification."
    )
    unit = EvidenceUnit(
        id=stable_id("evu", extraction.id, "1", hashlib.sha256(passage.encode()).hexdigest()),
        extraction_artifact_id=extraction.id,
        ordinal=1,
        section_path=("Results",),
        page_start=1,
        page_end=1,
        text=passage,
        temporal_safety_class=TemporalSafetyClass.DETERMINISTIC,
    )
    anchor = EvidenceAnchor(evidence_unit_id=unit.id, locator="page 1, Results, paragraph 1", quote=passage)
    evidence = EvidenceRecord(
        id=stable_id("evr", version.id, "estimate", "baseline-four-quarter"),
        work_id=work.id,
        document_version_id=version.id,
        record_type="empirical_estimate",
        schema_version="eeg.macro-evidence.v1",
        fields=(
            _field(
                "estimand",
                "Four-quarter bank-lending response",
                "four_quarter_bank_lending_response",
                None,
                anchor,
            ),
            _field("treatment", "100-basis-point monetary policy tightening", 100, "basis_points", anchor),
            _field("estimate", "-1.5 percent", -1.5, "percent", anchor),
            _field("standard_error", "0.4 percentage point", 0.4, "percentage_point", anchor),
            _field("horizon", "four quarters", 4, "quarters", anchor),
            _field("identification_strategy", "local projections", "local_projections", None, anchor),
        ),
    )
    member = SnapshotMember(
        work_id=work.id,
        document_version_id=version.id,
        extraction_artifact_id=extraction.id,
    )
    snapshot_identity = {
        "schema_version": "eeg.corpus-snapshot.v1",
        "policies": [policy.id],
        "members": [member],
    }
    snapshot_hash = record_sha256(snapshot_identity)
    snapshot = CorpusSnapshot(
        id=stable_id("snp", snapshot_hash),
        schema_version="eeg.corpus-snapshot.v1",
        created_at=_OBSERVED_AT,
        source_policy_version_ids=(policy.id,),
        members=(member,),
        manifest_sha256=snapshot_hash,
    )
    return EvidenceBundle(
        sources=(source,),
        source_policies=(policy,),
        retrieval_events=(retrieval,),
        raw_assets=(raw_asset,),
        work=work,
        document_versions=(version,),
        manifestations=(manifestation,),
        extraction_artifacts=(extraction,),
        evidence_units=(unit,),
        evidence_records=(evidence,),
        snapshot=snapshot,
    )


def _field(
    name: str,
    display: str,
    normalized: str | int | float,
    units: str | None,
    anchor: EvidenceAnchor,
) -> EvidenceFieldRevision:
    """Create one observed fixture field with an exact source anchor."""
    return EvidenceFieldRevision(
        id=stable_id("evf", name, display, anchor.evidence_unit_id),
        field_name=name,
        display_value=display,
        normalized_value=normalized,
        units=units,
        applicability="baseline local-projection specification; synthetic data",
        state=EvidenceState.OBSERVED,
        lineage_method="fixture_transcription",
        confidence=1.0,
        anchors=(anchor,),
    )
