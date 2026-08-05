"""Local operational readiness and promotion-manifest controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from shutil import disk_usage

from economic_evidence_graph.domain import IndexGeneration
from economic_evidence_graph.migrations import head_revision
from economic_evidence_graph.ports import ArtifactStore, CatalogRepository, Clock, SearchIndex, SearchRepository
from economic_evidence_graph.serialization import JsonValue, record_sha256


class ReadinessStatus(StrEnum):
    """One local operational check state."""

    # This is an operational status rather than a credential.
    PASS = "pass"  # nosec B105
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    """Content-minimized local operational check."""

    name: str
    status: ReadinessStatus
    detail: str


@dataclass(frozen=True, slots=True)
class LocalReadinessReport:
    """Machine-readable local service acceptance report."""

    schema_version: str
    ready: bool
    profile: str
    migration_revision: str
    checks: tuple[ReadinessCheck, ...]
    limitations: tuple[str, ...]


class LocalReadinessService:
    """Verify local providers and capacity without exposing research content."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        artifacts: ArtifactStore,
        search_repository: SearchRepository,
        search_index: SearchIndex,
        artifact_root: Path,
        minimum_free_space_gib: int,
        database_revision: Callable[[], str | None],
    ) -> None:
        self._catalog = catalog
        self._artifacts = artifacts
        self._search_repository = search_repository
        self._search_index = search_index
        self._artifact_root = artifact_root
        self._minimum_free_space_gib = minimum_free_space_gib
        self._database_revision = database_revision

    def report(self) -> LocalReadinessReport:
        """Run provider, index, migration, and disk-headroom checks."""
        checks = [
            self._provider_check("postgresql", self._catalog.check),
            self._provider_check("artifact_store", self._artifacts.check),
            self._provider_check("opensearch", self._search_index.check),
        ]
        expected_revision = head_revision()
        actual_revision = self._database_revision()
        checks.append(
            ReadinessCheck(
                "database_migration",
                ReadinessStatus.PASS if actual_revision == expected_revision else ReadinessStatus.FAIL,
                f"{actual_revision or 'unversioned'}; expected {expected_revision}",
            )
        )
        generation = self._search_repository.latest_index_generation()
        if generation is None:
            checks.append(ReadinessCheck("active_index_generation", ReadinessStatus.FAIL, "not built"))
        else:
            checks.append(
                ReadinessCheck(
                    "active_index_generation",
                    ReadinessStatus.PASS,
                    f"{generation.id}: {generation.work_document_count} works, "
                    f"{generation.evidence_document_count} evidence units",
                )
            )
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        free_gib = disk_usage(self._artifact_root).free / (1024**3)
        disk_status = ReadinessStatus.PASS if free_gib >= self._minimum_free_space_gib else ReadinessStatus.FAIL
        checks.append(
            ReadinessCheck(
                "artifact_disk_headroom",
                disk_status,
                f"{free_gib:.1f} GiB free; minimum {self._minimum_free_space_gib} GiB",
            )
        )
        ready = all(item.status is not ReadinessStatus.FAIL for item in checks)
        return LocalReadinessReport(
            schema_version="eeg.local-readiness.v1",
            ready=ready,
            profile="local",
            migration_revision=expected_revision,
            checks=tuple(checks),
            limitations=(
                "local actor attribution is not enterprise authentication",
                "high availability, institutional backup targets, and AWS controls are not asserted",
                "manual accessibility and recovery exercises remain operator acceptance activities",
            ),
        )

    @staticmethod
    def _provider_check(name: str, check: Callable[[], None]) -> ReadinessCheck:
        """Normalize one provider check without emitting endpoint or credential details."""
        try:
            check()
        except Exception as error:  # diagnostic boundary must report unavailable providers
            return ReadinessCheck(name, ReadinessStatus.FAIL, type(error).__name__)
        return ReadinessCheck(name, ReadinessStatus.PASS, "available")


class PromotionManifestService:
    """Seal local compatibility metadata before any future provider transfer."""

    def __init__(self, *, search_repository: SearchRepository, source_registry_path: Path, clock: Clock) -> None:
        self._search_repository = search_repository
        self._source_registry_path = source_registry_path
        self._clock = clock

    def create(self) -> dict[str, JsonValue]:
        """Create a checksum-addressed local promotion manifest."""
        generation = self._search_repository.latest_index_generation()
        if generation is None:
            raise ValueError("an active index generation is required before promotion")
        identity: dict[str, JsonValue] = {
            "schema_version": "eeg.promotion-manifest.v1",
            "created_at": self._clock.now().isoformat().replace("+00:00", "Z"),
            "source_profile": "local",
            "target_profile": "unassigned",
            "migration_revision": head_revision(),
            "corpus_snapshot_id": generation.corpus_snapshot_id,
            "index_generation": _generation_manifest(generation),
            "source_registry_sha256": sha256(self._source_registry_path.read_bytes()).hexdigest(),
            "compatibility_contracts": {
                "api": "eeg.api.v1",
                "agent_tools": "eeg.agent-tools.v1",
                "project_evidence": "eeg.project-evidence.v1",
                "project_export": "eeg.project-export.v1",
            },
            "required_pretransfer_reviews": [
                "source-policy transfer rights",
                "suppression state",
                "enterprise identity and network controls",
                "backup, recovery, encryption, and operational ownership",
            ],
        }
        return {**identity, "manifest_sha256": record_sha256(identity)}


def _generation_manifest(generation: IndexGeneration) -> dict[str, JsonValue]:
    """Select provider-neutral immutable generation metadata."""
    return {
        "id": generation.id,
        "mapping_version": generation.mapping_version,
        "retrieval_config_id": generation.retrieval_config_id,
        "embedding_config_id": generation.embedding_config_id,
        "manifest_sha256": generation.manifest_sha256,
        "work_document_count": generation.work_document_count,
        "evidence_document_count": generation.evidence_document_count,
        "created_at": generation.created_at.isoformat().replace("+00:00", "Z"),
        "activated_at": generation.activated_at.isoformat().replace("+00:00", "Z"),
    }
