"""Reproducible evidence-pack export service."""

from dataclasses import dataclass

from economic_evidence_graph.domain import EvidenceBundle, stable_id
from economic_evidence_graph.exceptions import ConflictError, EconomicEvidenceGraphError
from economic_evidence_graph.ports import ArtifactRef, ArtifactStore, CatalogRepository, Clock
from economic_evidence_graph.serialization import JsonValue, canonical_json_bytes, record_sha256, to_json_value

EXPORT_SCHEMA_VERSION = "eeg.export-manifest.v1"


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """A bounded request for a reproducible evidence pack."""

    work_ids: tuple[str, ...]
    snapshot_id: str | None = None
    format: str = "jsonl"

    def __post_init__(self) -> None:
        if not self.work_ids or any(not work_id.strip() for work_id in self.work_ids):
            raise EconomicEvidenceGraphError("export requests require at least one work identifier")
        if len(set(self.work_ids)) != len(self.work_ids):
            raise EconomicEvidenceGraphError("export work identifiers must be unique")
        if self.format != "jsonl":
            raise EconomicEvidenceGraphError("the platform skeleton supports only the jsonl export format")


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Stored export manifest and its constituent artifacts."""

    manifest_id: str
    snapshot_id: str
    manifest: ArtifactRef
    files: tuple[ArtifactRef, ...]


class EvidencePackExporter:
    """Create provenance-complete, checksum-addressed JSON Lines exports."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        artifacts: ArtifactStore,
        clock: Clock,
    ) -> None:
        self._catalog = catalog
        self._artifacts = artifacts
        self._clock = clock

    def export(self, request: ExportRequest) -> ExportResult:
        """Create and store a deterministic evidence pack.

        Args:
            request: Work and snapshot selection for the evidence pack.

        Returns:
            Content-addressed references to the manifest and data files.

        Raises:
            ConflictError: If selected works do not share the requested snapshot.
        """
        bundles = tuple(self._catalog.get_bundle(work_id) for work_id in sorted(request.work_ids))
        snapshot_ids = {bundle.snapshot.id for bundle in bundles}
        if request.snapshot_id is not None:
            snapshot_ids.add(request.snapshot_id)
        if len(snapshot_ids) != 1:
            raise ConflictError("all exported works must resolve to one corpus snapshot")
        snapshot_id = next(iter(snapshot_ids))
        if any(bundle.snapshot.id != snapshot_id for bundle in bundles):
            raise ConflictError("a selected work is not a member of the requested corpus snapshot")

        collections = _export_collections(bundles)
        named_files: list[tuple[str, ArtifactRef]] = []
        for name, records in collections:
            content = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
            named_files.append((name, self._artifacts.put_bytes(content, media_type="application/x-ndjson")))

        file_manifest: list[dict[str, JsonValue]] = [
            {
                "name": name,
                "uri": ref.uri,
                "sha256": ref.sha256,
                "media_type": ref.media_type,
                "byte_length": ref.byte_length,
            }
            for name, ref in named_files
        ]
        manifest_identity: dict[str, JsonValue] = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "work_ids": to_json_value(sorted(request.work_ids)),
            "files": to_json_value(file_manifest),
            "created_at": to_json_value(self._clock.now()),
        }
        manifest_id = stable_id("exp", record_sha256(manifest_identity))
        policy_ids = sorted(
            {policy_id for bundle in bundles for policy_id in bundle.snapshot.source_policy_version_ids}
        )
        manifest: dict[str, JsonValue] = {
            **manifest_identity,
            "manifest_id": manifest_id,
            "source_policy_version_ids": to_json_value(policy_ids),
            "warnings": [],
        }
        manifest_ref = self._artifacts.put_bytes(canonical_json_bytes(manifest), media_type="application/json")
        return ExportResult(
            manifest_id=manifest_id,
            snapshot_id=snapshot_id,
            manifest=manifest_ref,
            files=tuple(ref for _, ref in named_files),
        )


def _export_collections(bundles: tuple[EvidenceBundle, ...]) -> tuple[tuple[str, list[JsonValue]], ...]:
    """Build stable record collections from work bundles."""
    return (
        ("works.jsonl", [to_json_value(bundle.work) for bundle in bundles]),
        (
            "document_versions.jsonl",
            [
                to_json_value(item)
                for bundle in bundles
                for item in sorted(bundle.document_versions, key=lambda row: row.id)
            ],
        ),
        (
            "manifestations.jsonl",
            [
                to_json_value(item)
                for bundle in bundles
                for item in sorted(bundle.manifestations, key=lambda row: row.id)
            ],
        ),
        (
            "evidence_units.jsonl",
            [
                to_json_value(item)
                for bundle in bundles
                for item in sorted(bundle.evidence_units, key=lambda row: row.id)
            ],
        ),
        (
            "evidence_records.jsonl",
            [
                to_json_value(item)
                for bundle in bundles
                for item in sorted(bundle.evidence_records, key=lambda row: row.id)
            ],
        ),
    )
