"""Persistence boundary for collaborative projects and evidence review."""

from typing import Protocol

from economic_evidence_graph.domain import (
    EvidenceTable,
    EvidenceTableRow,
    PolicyLink,
    Project,
    ProjectBundle,
    ProjectEvidenceRecord,
    ProjectExport,
    ProjectMember,
    ProjectNote,
    ProjectSearchRun,
    ReviewEvent,
    ScreeningEvent,
)


class ProjectRepository(Protocol):
    """Atomic project mutations with optimistic revisions and immutable history."""

    def create_project(self, item: Project, owner: ProjectMember) -> Project:
        """Create a project and its owner membership idempotently."""
        ...

    def get_project(self, project_id: str) -> Project:
        """Return one project or raise when absent."""
        ...

    def list_projects(self, actor_id: str) -> tuple[Project, ...]:
        """Return projects visible to one collaborator."""
        ...

    def get_project_bundle(self, project_id: str) -> ProjectBundle:
        """Return complete project state and append-only history."""
        ...

    def put_project_member(self, item: ProjectMember, *, expected_revision: int) -> Project:
        """Add one member and bump the project revision atomically."""
        ...

    def put_project_search_run(self, item: ProjectSearchRun, *, expected_revision: int) -> Project:
        """Attach an immutable saved search and bump the project revision."""
        ...

    def put_screening_event(self, item: ScreeningEvent, *, expected_revision: int) -> Project:
        """Append a screening decision and bump the project revision."""
        ...

    def put_project_note(self, item: ProjectNote, *, expected_revision: int) -> Project:
        """Append a note and bump the project revision."""
        ...

    def put_project_evidence_record(self, item: ProjectEvidenceRecord, *, expected_revision: int) -> Project:
        """Persist an anchored evidence record and bump the project revision."""
        ...

    def put_evidence_table(self, item: EvidenceTable, *, expected_revision: int) -> Project:
        """Persist a comparison schema and bump the project revision."""
        ...

    def put_evidence_table_row(self, item: EvidenceTableRow, *, expected_revision: int) -> Project:
        """Persist a comparison row and bump the project revision."""
        ...

    def put_policy_link(self, item: PolicyLink, *, expected_revision: int) -> Project:
        """Persist a typed research-policy relationship and bump the revision."""
        ...

    def put_review_event(self, item: ReviewEvent, *, expected_revision: int) -> Project:
        """Append a review transition and bump the project revision."""
        ...

    def get_review_event(self, event_id: str) -> ReviewEvent | None:
        """Return one event for idempotent review retries."""
        ...

    def put_project_export(self, item: ProjectExport, *, expected_revision: int) -> Project:
        """Persist a checksum-addressed export and bump the project revision."""
        ...

    def get_project_export(self, export_id: str) -> ProjectExport:
        """Return one export record."""
        ...
