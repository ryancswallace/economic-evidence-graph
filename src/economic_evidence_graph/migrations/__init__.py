"""Alembic migration runtime packaged with the application."""

from importlib.resources import as_file, files

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def upgrade(database_url: str, revision: str = "head") -> None:
    """Upgrade a database using the package's immutable migration history.

    Args:
        database_url: SQLAlchemy PostgreSQL URL.
        revision: Alembic revision target, normally ``head``.
    """
    migrations = files("economic_evidence_graph.migrations")
    with as_file(migrations) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        command.upgrade(config, revision)


def head_revision() -> str:
    """Return the single packaged Alembic head revision."""
    migrations = files("economic_evidence_graph.migrations")
    with as_file(migrations) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:  # pragma: no cover - packaged migration history always has one head
        raise RuntimeError("packaged migration history has no head")
    return revision


__all__ = ["head_revision", "upgrade"]
