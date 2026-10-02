from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app.models import Base

EXPECTED = {
    "project",
    "member",
    "sprint",
    "work_item_snapshot",
    "event",
    "nudge",
    "outbox",
    "escalation",
    "llm_call",
}


def _upgrade(url: str):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return cfg


def test_migrations_create_all_tables_and_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    _upgrade(url)
    engine = create_engine(url)
    assert EXPECTED <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models drifted from migrations: {diff}"
