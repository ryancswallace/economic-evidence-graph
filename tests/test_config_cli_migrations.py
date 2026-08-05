"""Configuration, migration packaging, bootstrap, and operator CLI tests."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from economic_evidence_graph import cli
from economic_evidence_graph.application import IngestionSummary
from economic_evidence_graph.bootstrap import (
    create_local_container,
    create_local_corpus_container,
    require_connector,
)
from economic_evidence_graph.config import Settings
from economic_evidence_graph.exceptions import ConfigurationError
from economic_evidence_graph.migrations import upgrade


def _valid_environment(tmp_path: Path) -> dict[str, str]:
    """Return a complete non-secret settings mapping."""
    return {
        "EEG_PROFILE": "local",
        "EEG_DATABASE_URL": "postgresql+psycopg://eeg:local-pass@127.0.0.1:5432/eeg",  # pragma: allowlist secret
        "EEG_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "EEG_OPENSEARCH_URL": "http://127.0.0.1:9200",
        "EEG_API_HOST": "127.0.0.1",
        "EEG_API_PORT": "8123",
    }


def test_settings_load_local_profile_and_redact_password(tmp_path: Path) -> None:
    """Only explicit local settings enter the initial composition root."""
    settings = Settings.from_environment(_valid_environment(tmp_path))
    assert settings.profile == "local"
    assert settings.api_port == 8123
    assert settings.artifact_root == tmp_path / "artifacts"
    assert settings.redacted_database_url == "postgresql+psycopg://eeg:***@127.0.0.1:5432/eeg"
    without_password = Settings.from_environment(
        {**_valid_environment(tmp_path), "EEG_DATABASE_URL": "postgresql+psycopg://eeg@127.0.0.1/eeg"}
    )
    assert without_password.redacted_database_url == without_password.database_url
    assert "core_api_key" not in repr(settings)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"EEG_PROFILE": "aws"}, "only EEG_PROFILE=local"),
        ({"EEG_DATABASE_URL": ""}, "required"),
        ({"EEG_DATABASE_URL": "sqlite:///bad.db"}, r"postgresql\+psycopg"),
        ({"EEG_API_PORT": "not-a-number"}, "integer"),
        ({"EEG_API_PORT": "70000"}, "between"),
        ({"EEG_CRAWLER_EMAIL": "invalid"}, "contact email"),
        ({"EEG_REQUEST_TIMEOUT_SECONDS": "bad"}, "numeric"),
        ({"EEG_REQUEST_TIMEOUT_SECONDS": "0"}, "between"),
        ({"EEG_MINIMUM_FREE_SPACE_GIB": "bad"}, "integer"),
        ({"EEG_MINIMUM_FREE_SPACE_GIB": "0"}, "between"),
    ],
)
def test_settings_reject_invalid_local_configuration(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    """Configuration errors fail before opening network or storage providers."""
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environment({**_valid_environment(tmp_path), **changes})


def test_local_container_is_postgres_and_filesystem_backed(tmp_path: Path) -> None:
    """The real composition root does not silently substitute SQLite or memory providers."""
    settings = Settings.from_environment(_valid_environment(tmp_path))
    container = create_local_container(settings)
    assert type(container.catalog_repository).__name__ == "SqlRepository"
    assert type(container.artifacts).__name__ == "FileArtifactStore"
    container.close()


def test_local_corpus_container_wires_all_open_source_components(tmp_path: Path) -> None:
    """The production composition exposes every connector and keeps CORE optional by credential."""
    settings = Settings.from_environment(
        {
            **_valid_environment(tmp_path),
            "EEG_CORE_API_KEY": "configured-for-test",  # pragma: allowlist secret
            "EEG_SOURCE_REGISTRY": str(Path(__file__).parents[1] / "config/source-registry.toml"),
        }
    )
    container = create_local_corpus_container(settings)
    assert set(container.connectors) == {"repec", "crossref", "core", "boston_fed", "board_fomc"}
    assert require_connector(container, "core").source_code == "core"
    with pytest.raises(ConfigurationError, match="not configured"):
        require_connector(container, "missing")
    container.close()


def test_settings_loads_local_env_file_without_overriding_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local secrets may come from an ignored env file and never override process values."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "# local only",
                "EEG_CORE_API_KEY='from-file'",  # pragma: allowlist secret
                "EEG_DATABASE_URL=postgresql+psycopg://eeg@127.0.0.1/eeg",
            )
        )
    )
    monkeypatch.setenv("EEG_ENV_FILE", str(env_file))
    monkeypatch.setenv("EEG_DATABASE_URL", "postgresql+psycopg://process@127.0.0.1/eeg")
    settings = Settings.from_environment()
    assert settings.core_api_key == "from-file"  # pragma: allowlist secret
    assert "process@" in settings.database_url


def test_packaged_migration_builds_the_canonical_schema(tmp_path: Path) -> None:
    """An empty database upgrades from packaged immutable migrations."""
    database = tmp_path / "migration.db"
    url = f"sqlite+pysqlite:///{database}"
    upgrade(url)
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {
        "alembic_version",
        "source",
        "retrieval_event",
        "work",
        "document_version",
        "evidence_unit",
        "evidence_record",
        "corpus_snapshot",
        "job",
        "outbox_event",
        "policy_decision",
        "acquisition_failure",
        "source_metadata_record",
        "normalized_document",
        "index_generation",
        "quarantine_record",
        "search_run",
        "project",
        "project_member",
        "project_search_run",
        "screening_event",
        "project_note",
        "project_evidence_record",
        "evidence_table",
        "evidence_table_row",
        "policy_link",
        "review_event",
        "project_export",
        "suppression_record",
        "tool_invocation",
    } <= tables
    upgrade(url)


def test_cli_show_config_and_serve_are_non_destructive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator inspection redacts secrets and serve delegates explicit bind settings."""
    settings = Settings.from_environment(_valid_environment(tmp_path))
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: calls.append({"args": args, **kwargs}))

    assert cli.main(["show-config"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["database_url"].endswith("eeg:***@127.0.0.1:5432/eeg")
    assert "local-pass" not in json.dumps(shown)
    assert cli.main(["serve"]) == 0
    assert calls == [
        {
            "args": ("economic_evidence_graph.api:create_app",),
            "factory": True,
            "host": "127.0.0.1",
            "port": 8123,
        }
    ]


def test_cli_migrate_load_and_platform_smoke(
    monkeypatch: pytest.MonkeyPatch,
    memory_system: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Local operator commands cover migration and the deterministic Step 1 gate."""
    settings = Settings.from_environment(_valid_environment(tmp_path))
    migrations: list[str] = []
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "upgrade", lambda url: migrations.append(url))
    monkeypatch.setattr(cli, "create_local_container", lambda _: memory_system.container)  # type: ignore[attr-defined]

    assert cli.main(["migrate"]) == 0
    assert cli.main(["load-fixture"]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["work_id"] == memory_system.bundle.work.id  # type: ignore[attr-defined]
    assert cli.main(["platform-smoke"]) == 0
    smoked = json.loads(capsys.readouterr().out)
    assert smoked["job_state"] == "succeeded"
    assert len(migrations) == 3


def test_cli_ingestion_commands_are_bounded_and_close_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator CLI composes, runs, reports, and closes one bounded source page."""
    settings = Settings.from_environment(_valid_environment(tmp_path))

    class Ingestion:
        def ingest_page(self, connector: object, *, cursor: str | None, limit: int) -> IngestionSummary:
            assert connector == "connector"
            assert cursor == "cursor"
            assert limit == 2
            return IngestionSummary("crossref", 2, 1, 1, 0, 0, "next")

    class Corpus:
        ingestion = Ingestion()
        closed = False

        def close(self) -> None:
            self.closed = True

    corpus = Corpus()
    monkeypatch.setattr(cli.Settings, "from_environment", lambda: settings)
    monkeypatch.setattr(cli, "upgrade", lambda _url: None)
    monkeypatch.setattr(cli, "create_local_corpus_container", lambda _settings: corpus)
    monkeypatch.setattr(cli, "require_connector", lambda _container, _source: "connector")

    assert cli.main(["ingest-source", "--source", "crossref", "--cursor", "cursor", "--limit", "2"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["next_cursor"] == "next"
    assert corpus.closed
