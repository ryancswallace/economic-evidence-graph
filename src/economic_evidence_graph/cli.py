"""Local operator command-line interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import uvicorn

from economic_evidence_graph.application import (
    ExportRequest,
    LocalReadinessService,
    PromotionManifestService,
)
from economic_evidence_graph.bootstrap import create_local_container, create_local_corpus_container, require_connector
from economic_evidence_graph.config import Settings
from economic_evidence_graph.domain import HistoricalCutoff, SearchFilters, SearchMode, SearchRequest
from economic_evidence_graph.exceptions import ConfigurationError
from economic_evidence_graph.fixtures import build_platform_fixture
from economic_evidence_graph.migrations import upgrade
from economic_evidence_graph.providers import SystemClock
from economic_evidence_graph.serialization import to_json_value


def main(argv: Sequence[str] | None = None) -> int:
    """Run a local API, migration, or acceptance-fixture operation.

    Args:
        argv: Optional arguments excluding the executable name.

    Returns:
        Process exit status.
    """
    parser = _parser()
    arguments = parser.parse_args(argv)
    settings = Settings.from_environment()

    if arguments.command == "show-config":
        print(
            json.dumps(
                {
                    "profile": settings.profile,
                    "database_url": settings.redacted_database_url,
                    "artifact_root": str(settings.artifact_root),
                    "opensearch_url": settings.opensearch_url,
                    "api_host": settings.api_host,
                    "api_port": settings.api_port,
                    "crawler_email": settings.crawler_email,
                    "core_api_key_configured": settings.core_api_key is not None,
                    "source_registry": str(settings.source_registry_path),
                    "request_timeout_seconds": settings.request_timeout_seconds,
                    "minimum_free_space_gib": settings.minimum_free_space_gib,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if arguments.command == "migrate":
        upgrade(settings.database_url)
        return 0

    if arguments.command == "serve":
        uvicorn.run(
            "economic_evidence_graph.api:create_app",
            factory=True,
            host=settings.api_host,
            port=settings.api_port,
        )
        return 0

    if arguments.command in {"readiness", "promotion-manifest", "export-openapi", "tool-audit"}:
        container = create_local_container(settings)
        try:
            if arguments.command == "readiness":
                repository = container.catalog_repository
                database_revision = getattr(repository, "current_migration_revision", lambda: None)
                if container.search_index is None:
                    raise ConfigurationError("search index is not configured")
                report = LocalReadinessService(
                    catalog=repository,
                    artifacts=container.artifacts,
                    search_repository=repository,  # type: ignore[arg-type]
                    search_index=container.search_index,
                    artifact_root=settings.artifact_root,
                    minimum_free_space_gib=settings.minimum_free_space_gib,
                    database_revision=database_revision,
                ).report()
                print(json.dumps(to_json_value(report), indent=2, sort_keys=True))
                return 0 if report.ready else 1
            if arguments.command == "promotion-manifest":
                manifest = PromotionManifestService(
                    search_repository=container.catalog_repository,  # type: ignore[arg-type]
                    source_registry_path=settings.source_registry_path,
                    clock=SystemClock(),
                ).create()
                _write_json(arguments.output, manifest)
                print(json.dumps({"manifest_sha256": manifest["manifest_sha256"], "output": str(arguments.output)}))
                return 0
            if arguments.command == "export-openapi":
                from economic_evidence_graph.api import create_app

                _write_json(arguments.output, create_app(container).openapi())
                print(json.dumps({"output": str(arguments.output)}))
                return 0
            audit_repository = container.catalog_repository
            list_invocations = getattr(audit_repository, "list_tool_invocations", None)
            if list_invocations is None:
                raise ConfigurationError("tool audit repository is not configured")
            print(json.dumps(to_json_value(list_invocations(limit=arguments.limit)), indent=2, sort_keys=True))
            return 0
        finally:
            container.close()

    if arguments.command in {"ingest-source", "connector-smoke"}:
        upgrade(settings.database_url)
        corpus = create_local_corpus_container(settings)
        try:
            connector = require_connector(corpus, arguments.source)
            summary = corpus.ingestion.ingest_page(
                connector,
                cursor=arguments.cursor,
                limit=arguments.limit,
            )
            print(json.dumps(to_json_value(summary), indent=2, sort_keys=True))
            return 0 if summary.failed_records == 0 else 1
        finally:
            corpus.close()

    if arguments.command in {"rebuild-search", "search"}:
        upgrade(settings.database_url)
        container = create_local_container(settings)
        try:
            if arguments.command == "rebuild-search":
                if container.indexer is None:
                    raise ConfigurationError("search indexer is not configured")
                generation = container.indexer.rebuild()
                print(json.dumps(to_json_value(generation), indent=2, sort_keys=True))
                return 0
            if container.retrieval is None:
                raise ConfigurationError("search retrieval is not configured")
            result = container.retrieval.search(
                SearchRequest(
                    query=arguments.query,
                    modes=tuple(SearchMode(item) for item in (arguments.mode or ("lexical", "semantic"))),
                    filters=SearchFilters(
                        source_codes=tuple(arguments.source or ()),
                        published_from=arguments.published_from,
                        published_to=arguments.published_to,
                    ),
                    historical_cutoff=(None if arguments.as_of is None else HistoricalCutoff(arguments.as_of)),
                    collapse_versions=not arguments.all_versions,
                    limit=arguments.limit,
                    save_run=arguments.save_run,
                )
            )
            print(json.dumps(to_json_value(result), indent=2, sort_keys=True))
            return 0
        finally:
            container.close()

    if arguments.command == "suppress":
        upgrade(settings.database_url)
        corpus = create_local_corpus_container(settings)
        try:
            item = corpus.ingestion.suppress(
                scope_type=arguments.scope_type,
                scope_value=arguments.scope_value,
                reason=arguments.reason,
                physical_deletion_requested=arguments.request_physical_deletion,
            )
            print(json.dumps(to_json_value(item), indent=2, sort_keys=True))
            return 0
        finally:
            corpus.close()

    upgrade(settings.database_url)
    container = create_local_container(settings)
    fixture = build_platform_fixture(container.artifacts)
    container.catalog.ingest(fixture)

    if arguments.command == "load-fixture":
        print(json.dumps({"work_id": fixture.work.id, "snapshot_id": fixture.snapshot.id}, sort_keys=True))
        return 0

    if arguments.command == "platform-smoke":
        completed = container.jobs.submit_export(
            request=ExportRequest(work_ids=(fixture.work.id,), snapshot_id=fixture.snapshot.id),
            idempotency_key="platform-smoke-v1",
        )
        print(
            json.dumps(
                {
                    "work_id": fixture.work.id,
                    "snapshot_id": fixture.snapshot.id,
                    "job_id": completed.id,
                    "job_state": completed.state.value,
                    "output": None if completed.output is None else dict(completed.output),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if completed.state.value == "succeeded" else 1

    parser.error(f"unsupported command: {arguments.command}")
    return 2  # pragma: no cover - argparse exits before reaching this statement


def _parser() -> argparse.ArgumentParser:
    """Build the stable local operator command tree."""
    parser = argparse.ArgumentParser(prog="eeg", description="Economic Evidence Graph local operator CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show-config", help="print non-secret effective local configuration")
    commands.add_parser("migrate", help="upgrade PostgreSQL to the current schema")
    commands.add_parser("serve", help="run the local HTTP API")
    commands.add_parser("readiness", help="run privacy-safe local provider and capacity checks")
    promotion = commands.add_parser("promotion-manifest", help="seal provider-neutral local promotion metadata")
    promotion.add_argument("--output", type=Path, required=True)
    openapi = commands.add_parser("export-openapi", help="write the current versioned OpenAPI contract")
    openapi.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser("tool-audit", help="list privacy-safe agent-tool invocation records")
    audit.add_argument("--limit", type=int, choices=range(1, 1001), default=100)
    commands.add_parser("load-fixture", help="load the synthetic Step 1 fixture")
    commands.add_parser("platform-smoke", help="migrate, load, retrieve, and export the Step 1 fixture")
    ingest = commands.add_parser("ingest-source", help="ingest one bounded page from an approved source")
    _add_ingestion_arguments(ingest, default_limit=25)
    smoke = commands.add_parser("connector-smoke", help="run a 10-record live source acceptance slice")
    _add_ingestion_arguments(smoke, default_limit=10)
    suppress = commands.add_parser("suppress", help="create an immediate acquisition and delivery tombstone")
    suppress.add_argument("--scope-type", choices=("source_record", "url", "asset"), required=True)
    suppress.add_argument("--scope-value", required=True)
    suppress.add_argument("--reason", required=True)
    suppress.add_argument("--request-physical-deletion", action="store_true")
    commands.add_parser("rebuild-search", help="build and atomically activate a complete search generation")
    search = commands.add_parser("search", help="run explainable hybrid retrieval from the terminal")
    search.add_argument("query")
    search.add_argument(
        "--mode",
        action="append",
        choices=("lexical", "semantic", "structured"),
        help="candidate channel; repeat to combine channels",
    )
    search.add_argument(
        "--source",
        action="append",
        choices=("repec", "crossref", "core", "boston_fed", "board_fomc"),
    )
    search.add_argument("--published-from", type=date.fromisoformat)
    search.add_argument("--published-to", type=date.fromisoformat)
    search.add_argument("--as-of", type=_utc_datetime, help="strict UTC cutoff, for example 2020-03-15T00:00:00Z")
    search.add_argument("--limit", type=int, choices=range(1, 51), default=20)
    search.add_argument("--all-versions", action="store_true")
    search.add_argument("--save-run", action="store_true")
    return parser


def _add_ingestion_arguments(parser: argparse.ArgumentParser, *, default_limit: int) -> None:
    """Add bounded source ingestion arguments."""
    parser.add_argument(
        "--source",
        choices=("repec", "crossref", "core", "boston_fed", "board_fomc"),
        required=True,
    )
    parser.add_argument("--cursor")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=default_limit)


def _utc_datetime(value: str) -> datetime:
    """Parse an aware UTC CLI timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware UTC")
    return parsed


def _write_json(path: Path, value: object) -> None:
    """Write one deterministic JSON artifact to an explicit operator path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_json_value(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
