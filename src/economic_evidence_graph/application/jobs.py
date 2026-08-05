"""Idempotent in-process job foundation."""

from dataclasses import dataclass

from economic_evidence_graph.application.exports import EvidencePackExporter, ExportRequest
from economic_evidence_graph.domain import Job
from economic_evidence_graph.exceptions import EconomicEvidenceGraphError
from economic_evidence_graph.ports import Clock, IdFactory, JobRepository


@dataclass(frozen=True, slots=True)
class JobService:
    """Create and execute the initial export job type through durable state transitions."""

    jobs: JobRepository
    exporter: EvidencePackExporter
    clock: Clock
    ids: IdFactory

    def submit_export(self, *, request: ExportRequest, idempotency_key: str) -> Job:
        """Create or return an idempotent export job and execute it locally."""
        prior = self.jobs.find_by_idempotency_key(idempotency_key)
        if prior is not None:
            return prior

        job = Job.pending(
            id=self.ids.new("job"),
            kind="evidence_pack_export",
            idempotency_key=idempotency_key,
            input={
                "work_ids": ",".join(request.work_ids),
                "snapshot_id": request.snapshot_id,
                "format": request.format,
            },
            created_at=self.clock.now(),
        )
        self.jobs.add(job)
        running = job.start(self.clock.now())
        self.jobs.save(running)
        try:
            result = self.exporter.export(request)
        except EconomicEvidenceGraphError as error:
            failed = running.fail(self.clock.now(), code=error.code, message=str(error))
            self.jobs.save(failed)
            return failed

        succeeded = running.succeed(
            self.clock.now(),
            {
                "manifest_id": result.manifest_id,
                "manifest_uri": result.manifest.uri,
                "manifest_sha256": result.manifest.sha256,
                "manifest_byte_length": result.manifest.byte_length,
                "snapshot_id": result.snapshot_id,
                "file_count": len(result.files),
            },
        )
        self.jobs.save(succeeded)
        return succeeded

    def get(self, job_id: str) -> Job:
        """Return a job by identifier."""
        return self.jobs.get(job_id)
