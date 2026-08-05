"""Parser, orchestration, identity, quarantine, and persistence tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import create_engine

from economic_evidence_graph.application import CorpusIngestionService
from economic_evidence_graph.domain import (
    AcquiredAsset,
    ContentLink,
    IdentifierValue,
    IdentityCandidate,
    NormalizedDocument,
    NormalizedUnit,
    PolicyDecisionRecord,
    PolicyOutcome,
    QualityReport,
    QuarantineReason,
    QuarantineRecord,
    RequestTrace,
    RobotsObservation,
    SourceChangeEvent,
    SourceMetadataRecord,
)
from economic_evidence_graph.exceptions import CapacityExceededError, ConflictError, EconomicEvidenceGraphError
from economic_evidence_graph.parsers import DocumentParserRouter, ParsedDocument, ParseFailure
from economic_evidence_graph.policy import PolicyEngine, SourceRegistry
from economic_evidence_graph.ports import (
    AcquisitionBatch,
    AcquisitionCandidate,
    AcquisitionFailureCandidate,
    DiscoveryPage,
    DiscoveryRecord,
)
from economic_evidence_graph.providers import (
    DeterministicIdFactory,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryRepository,
)
from economic_evidence_graph.providers.postgres import SqlRepository
from economic_evidence_graph.providers.postgres.schema import metadata as sql_metadata

_ROOT = Path(__file__).parents[1]
_NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)


def _pdf(*, text: str | None = None, encrypted: bool = False) -> bytes:
    """Build a small PDF with optional extractable Helvetica text."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        safe = text.replace("(", "[").replace(")", "]")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_parser_routes_html_text_xml_json_and_pdf() -> None:
    """All approved deterministic parser routes create structural units."""
    router = DocumentParserRouter()
    html = router.parse(
        b"<html><body><h1>Findings</h1><p>Inflation declined.</p><script>ignore()</script></body></html>",
        media_type="application/octet-stream",
    )
    assert html.units[0].section_path == ("Findings",)
    assert "ignore" not in html.text
    text = router.parse(b"First paragraph.\n\nSecond paragraph.", media_type="text/plain")
    assert len(text.units) == 2
    xml = router.parse(b"<root><p>XML evidence</p></root>", media_type="application/xml")
    assert "XML evidence" in xml.text
    json_document = router.parse(b'{"b": 2, "a": 1}', media_type="application/json")
    assert json_document.text.index('"a"') < json_document.text.index('"b"')
    pdf = router.parse(
        _pdf(text="Monetary policy evidence with enough embedded text for extraction."),
        media_type="application/pdf",
    )
    assert pdf.quality.page_count == 1
    assert pdf.units[0].locator == "page:1"


@pytest.mark.parametrize(
    ("content", "media_type", "reason"),
    [
        (b"", "text/plain", QuarantineReason.EMPTY_TEXT),
        (b"not-json", "application/json", QuarantineReason.MALFORMED),
        (b"<broken", "application/xml", QuarantineReason.MALFORMED),
        (b"binary", "application/octet-stream", QuarantineReason.UNSUPPORTED_MEDIA_TYPE),
        (b"%PDF-broken", "application/pdf", QuarantineReason.MALFORMED),
    ],
)
def test_parser_quarantine_reasons_are_structured(
    content: bytes,
    media_type: str,
    reason: QuarantineReason,
) -> None:
    """Poison inputs fail with stable review-queue reasons."""
    with pytest.raises(ParseFailure) as captured:
        DocumentParserRouter().parse(content, media_type=media_type)
    assert captured.value.reason is reason


def test_pdf_security_encryption_ocr_and_size_routes() -> None:
    """PDF active content, encryption, image-only pages, and large assets never parse silently."""
    router = DocumentParserRouter(maximum_bytes=100_000)
    with pytest.raises(ParseFailure) as encrypted:
        router.parse(_pdf(encrypted=True), media_type="application/pdf")
    assert encrypted.value.reason is QuarantineReason.ENCRYPTED
    with pytest.raises(ParseFailure) as scanned:
        router.parse(_pdf(), media_type="application/pdf")
    assert scanned.value.reason is QuarantineReason.OCR_REQUIRED
    assert scanned.value.retryable
    with pytest.raises(ParseFailure) as active:
        router.parse(b"%PDF-1.4 /JavaScript", media_type="application/pdf")
    assert active.value.reason is QuarantineReason.SECURITY_REJECTED
    with pytest.raises(ParseFailure) as too_large:
        DocumentParserRouter(maximum_bytes=3).parse(b"1234", media_type="text/plain")
    assert too_large.value.reason is QuarantineReason.SIZE_LIMIT
    with pytest.raises(ValueError, match="positive"):
        DocumentParserRouter(maximum_bytes=0)


def test_optional_ocr_provider_is_used_for_scanned_pdf() -> None:
    """Authorized open-source OCR remains an injectable provider rather than hidden behavior."""
    expected = DocumentParserRouter().parse(b"OCR output paragraph.", media_type="text/plain")

    class Ocr:
        def extract_pdf(self, content: bytes) -> ParsedDocument:
            assert content.startswith(b"%PDF")
            return expected

    assert DocumentParserRouter(ocr=Ocr()).parse(_pdf(), media_type="application/pdf") == expected


class _Connector:
    """Deterministic connector fixture for application orchestration."""

    source_code = "crossref"

    def __init__(self, record: DiscoveryRecord, candidates: tuple[AcquisitionCandidate, ...]) -> None:
        self.record = record
        self.candidates = candidates

    def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        assert limit > 0
        return DiscoveryPage((self.record,), cursor, "one", ())

    def acquire(self, record: DiscoveryRecord) -> AcquisitionBatch:
        assert record == self.record
        return AcquisitionBatch(self.candidates, ())


def _record(identifier: str, *, doi: str = "10.1234/shared", raw: bytes = b"metadata") -> DiscoveryRecord:
    """Build one deterministic source observation."""
    return DiscoveryRecord(
        source_code="crossref",
        source_identifier=identifier,
        metadata_url="https://api.crossref.org/works/" + identifier,
        title="Monetary Policy Evidence",
        authors=("Ada Economist",),
        abstract="Abstract",
        published_at=date(2024, 1, 1),
        asserted_updated_at=_NOW,
        identifiers=(IdentifierValue("doi", doi),),
        content_links=(ContentLink("https://publisher.example/paper", "text/html", "linked_full_text"),),
        raw_metadata=raw,
        metadata_media_type="application/json",
        discovered_at=_NOW,
    )


def _candidate(record: DiscoveryRecord, content: bytes, media_type: str = "text/html") -> AcquisitionCandidate:
    """Build one governed acquisition fixture."""
    return AcquisitionCandidate(
        record=record,
        requested_url="https://publisher.example/paper",
        resolved_url="https://publisher.example/paper",
        media_type=media_type,
        content=content,
        retrieved_at=_NOW,
        http_status=200,
        license_uri=None,
        trace=RequestTrace((), ()),
    )


def _service(
    repository: object,
    *,
    maximum_records: int = 1_000,
    maximum_source_bytes: int = 10_737_418_240,
) -> CorpusIngestionService:
    """Compose the corpus service with deterministic local providers."""
    registry = SourceRegistry.load(_ROOT / "config/source-registry.toml")
    return CorpusIngestionService(
        repository=repository,  # type: ignore[arg-type]
        artifacts=InMemoryArtifactStore(),
        parsers=DocumentParserRouter(),
        policy=PolicyEngine(registry),
        clock=FrozenClock(_NOW),
        ids=DeterministicIdFactory("pipeline"),
        maximum_records=maximum_records,
        maximum_source_bytes=maximum_source_bytes,
    )


def test_corpus_ingestion_is_idempotent_and_preserves_unknown_rights() -> None:
    """Replaying an observation does not duplicate core effects or imply open rights."""
    repository = InMemoryRepository()
    record = _record("one")
    connector = _Connector(record, (_candidate(record, b"<html><body><h1>Results</h1><p>Evidence.</p></body></html>"),))
    service = _service(repository)

    first = service.ingest_page(connector, cursor=None, limit=1)
    second = service.ingest_page(connector, cursor=None, limit=1)

    assert first.normalized_documents == second.normalized_documents == 1
    assert len(repository.ingested_records) == 1
    result = repository.ingested_records[0]
    assert result.assets[0].rights_status == "rights_unknown"
    assert result.documents[0].units[0].section_path == ("Results",)
    assert result.change.kind.value == "new"


def test_corpus_ingestion_quarantines_poison_and_proposes_identity() -> None:
    """Scanned content is quarantined while exact DOI overlap becomes reviewable identity evidence."""
    repository = InMemoryRepository()
    service = _service(repository)
    first_record = _record("one")
    service.ingest_page(
        _Connector(first_record, (_candidate(first_record, b"First paragraph.", "text/plain"),)),
        cursor=None,
        limit=1,
    )
    second_record = _record("two", raw=b"different metadata")
    summary = service.ingest_page(
        _Connector(second_record, (_candidate(second_record, _pdf(), "application/pdf"),)),
        cursor=None,
        limit=1,
    )
    assert summary.quarantined_assets == 1
    second = next(item for item in repository.ingested_records if item.metadata.source_identifier == "two")
    assert second.quarantines[0].reason is QuarantineReason.OCR_REQUIRED
    assert second.identity_candidates[0].method == "exact_identifier"
    assert second.identity_candidates[0].hard_conflict is False


def test_corpus_service_isolates_record_failures() -> None:
    """One policy-invalid record increments failures instead of stopping the page."""
    bad = replace(_record("bad"), metadata_url="https://evil.example/metadata")
    summary = _service(InMemoryRepository()).ingest_page(_Connector(bad, ()), cursor=None, limit=1)
    assert summary.failed_records == 1
    assert summary.acquired_assets == 0
    assert summary.failure_codes == (("source_policy_denied", 1),)


def test_metadata_storage_uses_discovery_provenance_not_canonical_item_url() -> None:
    """An external canonical URL cannot invalidate metadata acquired from an approved API."""
    record = replace(_record("external"), metadata_url="https://doi.org/10.1234/shared")
    decision = PolicyDecisionRecord(
        "pol_discovery",
        "crossref",
        1,
        "discover_metadata",
        "https://api.crossref.org/works",
        "bibliographic_metadata",
        PolicyOutcome.ALLOW,
        "action_allowed",
        _NOW,
    )

    class TracedConnector(_Connector):
        def discover(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
            assert limit > 0
            return DiscoveryPage((self.record,), cursor, "one", (RequestTrace((decision,), ()),))

    repository = InMemoryRepository()
    summary = _service(repository).ingest_page(TracedConnector(record, ()), cursor=None, limit=1)
    assert summary.failed_records == 0
    assert repository.ingested_records[0].metadata.metadata_url.startswith("https://doi.org/")


def test_acquisition_failure_is_durable_without_rejecting_metadata() -> None:
    """A denied content link commits safe failure evidence beside valid metadata."""
    record = _record("content-denied")
    denial = PolicyDecisionRecord(
        "pol_denied",
        "crossref",
        1,
        "fetch_content",
        "https://publisher.example/paper",
        "linked_full_text",
        PolicyOutcome.DENY,
        "robots_disallowed",
        _NOW,
    )
    failure = AcquisitionFailureCandidate(
        record,
        "https://publisher.example/paper",
        "source_policy_denied",
        "robots policy denied HTTP request",
        _NOW,
        RequestTrace((denial,), ()),
    )

    class FailedAcquisitionConnector(_Connector):
        def acquire(self, record: DiscoveryRecord) -> AcquisitionBatch:
            assert record == self.record
            return AcquisitionBatch((), (failure,))

    repository = InMemoryRepository()
    summary = _service(repository).ingest_page(FailedAcquisitionConnector(record, ()), cursor=None, limit=1)
    assert summary.failed_records == 0
    assert summary.acquisition_failure_codes == (("source_policy_denied", 1),)
    assert repository.ingested_records[0].acquisition_failures[0].detail.startswith("robots policy")


def test_suppression_tombstone_blocks_future_acquisition() -> None:
    """A durable source-record tombstone prevents connector content access immediately."""
    repository = InMemoryRepository()
    service = _service(repository)
    item = service.suppress(
        scope_type="source_record",
        scope_value="crossref:suppressed",
        reason="pilot takedown test",
    )
    assert repository.is_suppressed(item.scope_type, item.scope_value)
    record = _record("suppressed")

    class NeverAcquire(_Connector):
        def acquire(self, record: DiscoveryRecord) -> AcquisitionBatch:
            raise AssertionError("suppression must precede acquisition")

    summary = service.ingest_page(NeverAcquire(record, ()), cursor=None, limit=1)
    assert summary.acquired_assets == 0
    assert repository.ingested_records[0].assets == ()
    with pytest.raises(EconomicEvidenceGraphError, match="scope"):
        service.suppress(scope_type="bad", scope_value="x", reason="reason")


def test_local_pilot_record_and_byte_ceilings_are_enforced() -> None:
    """The 1,000/10 GiB defaults are configurable and checked before persistence."""
    repository = InMemoryRepository()
    service = _service(repository, maximum_records=1)
    record = _record("capacity-one")
    service.ingest_page(
        _Connector(record, (_candidate(record, b"one", "text/plain"),)),
        cursor=None,
        limit=5,
    )
    assert repository.corpus_usage() == (1, 3)
    with pytest.raises(CapacityExceededError, match="ceiling"):
        service.ingest_page(_Connector(_record("capacity-two"), ()), cursor=None, limit=1)

    limited = InMemoryRepository()
    summary = _service(limited, maximum_source_bytes=2).ingest_page(
        _Connector(record, (_candidate(record, b"three", "text/plain"),)),
        cursor=None,
        limit=1,
    )
    assert summary.failed_records == 1
    assert limited.corpus_usage() == (0, 0)
    with pytest.raises(ValueError, match="ceilings"):
        _service(InMemoryRepository(), maximum_records=0)


def test_sql_corpus_repository_round_trip_queries_and_conflict() -> None:
    """SQLite contract tests the same durable Step 2 schema used by PostgreSQL."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    sql_metadata.create_all(engine)
    repository = SqlRepository(engine)
    record = _record("sql")
    service = _service(repository)
    summary = service.ingest_page(
        _Connector(record, (_candidate(record, b"SQL-backed evidence paragraph.", "text/plain"),)),
        cursor=None,
        limit=1,
    )
    assert summary.normalized_documents == 1
    latest = repository.latest_source_record("crossref", "sql")
    assert latest is not None
    assert latest.metadata_sha256
    assert repository.identity_matches(latest.identifiers, exclude_record_id="none") == (latest,)
    assert repository.identity_matches((), exclude_record_id="none") == ()
    assert repository.corpus_usage() == (1, len(b"SQL-backed evidence paragraph."))
    suppression = service.suppress(scope_type="url", scope_value="https://x", reason="test")
    assert repository.is_suppressed("url", "https://x")
    repository.put_suppression(suppression)
    repository.close()


def test_governance_domain_validation_and_memory_conflicts() -> None:
    """Invalid audit inputs and conflicting immutable trace IDs are rejected."""
    with pytest.raises(EconomicEvidenceGraphError):
        PolicyDecisionRecord("", "crossref", 0, "", "", "", PolicyOutcome.DENY, "", _NOW)
    with pytest.raises(EconomicEvidenceGraphError):
        RobotsObservation("r", "x", "u", "t", _NOW, 99, "x" * 64, False, "bad")

    repository = InMemoryRepository()
    decision = PolicyDecisionRecord(
        "pol_1", "crossref", 1, "fetch_content", "https://x", "text", PolicyOutcome.ALLOW, "ok", _NOW
    )
    repository.put_governance_trace((decision,), ())
    repository.put_governance_trace((decision,), ())
    with pytest.raises(ConflictError):
        repository.put_governance_trace((replace(decision, reason="changed"),), ())


def test_corpus_domain_records_reject_invalid_lineage_and_quality() -> None:
    """Step 2 records enforce timestamps, hashes, locators, counts, and review fields."""
    sha = "a" * 64
    metadata = SourceMetadataRecord(
        "met_1",
        "crossref",
        "one",
        "https://api.crossref.org/works/one",
        "Title",
        ("Author",),
        None,
        None,
        None,
        (IdentifierValue("doi", "10/x"),),
        (ContentLink("https://x/p.pdf", "application/pdf", "full_text"),),
        _NOW,
        sha,
        "artifact://sha256/" + sha,
    )
    asset = AcquiredAsset(
        "ast_1",
        metadata.id,
        "https://x/p.pdf",
        "https://x/p.pdf",
        _NOW,
        200,
        "application/pdf",
        sha,
        1,
        "artifact://sha256/" + sha,
        "rights_unknown",
        None,
    )
    quality = QualityReport("pdf", 1, 10, 10.0, False, ())
    unit = NormalizedUnit("nru_1", "nrm_1", 0, ("Document",), "page:1", 1, 1, "Text")
    document = NormalizedDocument(
        "nrm_1", asset.id, "parser", "1", "schema", sha, "artifact://sha256/" + sha, _NOW, (unit,), quality
    )

    invalid_factories = (
        lambda: IdentifierValue("", "x"),
        lambda: ContentLink("", None, "x"),
        lambda: replace(metadata, title=""),
        lambda: replace(metadata, authors=("",)),
        lambda: replace(metadata, observed_at=_NOW.replace(tzinfo=None)),
        lambda: replace(metadata, metadata_sha256="bad"),
        lambda: replace(metadata, metadata_storage_uri=""),
        lambda: replace(asset, retrieved_at=_NOW.replace(tzinfo=None)),
        lambda: replace(asset, byte_length=-1),
        lambda: replace(unit, ordinal=-1),
        lambda: replace(unit, page_end=None),
        lambda: replace(unit, page_end=0),
        lambda: replace(quality, character_count=-1),
        lambda: replace(document, text_sha256="bad"),
        lambda: replace(document, units=()),
        lambda: QuarantineRecord("q", asset.id, QuarantineReason.MALFORMED, "", _NOW, False),
        lambda: IdentityCandidate("i", "l", "r", "", 2.0, (), False, _NOW),
        lambda: SourceChangeEvent("c", "s", "i", None, "m", "new", _NOW.replace(tzinfo=None)),  # type: ignore[arg-type]
    )
    for factory in invalid_factories:
        with pytest.raises(EconomicEvidenceGraphError):
            factory()
