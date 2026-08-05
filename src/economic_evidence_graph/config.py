"""Explicit local runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from economic_evidence_graph.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings for the initial local-only implementation."""

    profile: str
    database_url: str
    artifact_root: Path
    opensearch_url: str
    api_host: str
    api_port: int
    crawler_email: str
    core_api_key: str | None = field(repr=False)
    source_registry_path: Path
    request_timeout_seconds: float
    minimum_free_space_gib: int

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Settings:
        """Load settings from explicit ``EEG_`` environment variables.

        Args:
            environment: Optional mapping used instead of the process environment.

        Returns:
            Fully validated local settings.

        Raises:
            ConfigurationError: If a required value is absent or invalid.
        """
        values = _process_environment() if environment is None else environment
        profile = values.get("EEG_PROFILE", "local").strip()
        if profile != "local":
            raise ConfigurationError("the initial implementation supports only EEG_PROFILE=local")
        database_url = values.get("EEG_DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("EEG_DATABASE_URL is required for the PostgreSQL local profile")
        if not database_url.startswith("postgresql+psycopg://"):
            raise ConfigurationError("EEG_DATABASE_URL must use the postgresql+psycopg driver")
        artifact_root = Path(values.get("EEG_ARTIFACT_ROOT", ".eeg/artifacts"))
        opensearch_url = values.get("EEG_OPENSEARCH_URL", "http://127.0.0.1:9200").strip()
        api_host = values.get("EEG_API_HOST", "127.0.0.1").strip()
        try:
            api_port = int(values.get("EEG_API_PORT", "8000"))
        except ValueError as error:
            raise ConfigurationError("EEG_API_PORT must be an integer") from error
        if not 1 <= api_port <= 65535:
            raise ConfigurationError("EEG_API_PORT must be between 1 and 65535")
        crawler_email = values.get("EEG_CRAWLER_EMAIL", "rcswcsai@gmail.com").strip()
        if "@" not in crawler_email:
            raise ConfigurationError("EEG_CRAWLER_EMAIL must be a contact email")
        core_api_key = values.get("EEG_CORE_API_KEY", "").strip() or None
        source_registry_path = Path(values.get("EEG_SOURCE_REGISTRY", "config/source-registry.toml"))
        try:
            request_timeout_seconds = float(values.get("EEG_REQUEST_TIMEOUT_SECONDS", "30"))
        except ValueError as error:
            raise ConfigurationError("EEG_REQUEST_TIMEOUT_SECONDS must be numeric") from error
        if not 1 <= request_timeout_seconds <= 300:
            raise ConfigurationError("EEG_REQUEST_TIMEOUT_SECONDS must be between 1 and 300")
        try:
            minimum_free_space_gib = int(values.get("EEG_MINIMUM_FREE_SPACE_GIB", "20"))
        except ValueError as error:
            raise ConfigurationError("EEG_MINIMUM_FREE_SPACE_GIB must be an integer") from error
        if not 1 <= minimum_free_space_gib <= 1_024:
            raise ConfigurationError("EEG_MINIMUM_FREE_SPACE_GIB must be between 1 and 1024")
        return cls(
            profile=profile,
            database_url=database_url,
            artifact_root=artifact_root,
            opensearch_url=opensearch_url,
            api_host=api_host,
            api_port=api_port,
            crawler_email=crawler_email,
            core_api_key=core_api_key,
            source_registry_path=source_registry_path,
            request_timeout_seconds=request_timeout_seconds,
            minimum_free_space_gib=minimum_free_space_gib,
        )

    @property
    def redacted_database_url(self) -> str:
        """Return the database URL without a password."""
        parts = urlsplit(self.database_url)
        if parts.password is None:
            return self.database_url
        username = parts.username or ""
        hostname = parts.hostname or ""
        port = "" if parts.port is None else f":{parts.port}"
        return urlunsplit((parts.scheme, f"{username}:***@{hostname}{port}", parts.path, parts.query, parts.fragment))


def _process_environment() -> Mapping[str, str]:
    """Merge an optional local env file without logging or interpolating secrets."""
    values = dict(os.environ)
    path = Path(values.get("EEG_ENV_FILE", ".env"))
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigurationError(f"cannot read EEG_ENV_FILE: {path}") from error
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("EEG_"):
            values.setdefault(key, value.strip().strip("'\""))
    return values
