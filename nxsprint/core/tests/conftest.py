import os
import shutil
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.main import create_app
from app.settings import Mode, Settings

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "projects.example.yaml"
SECRET = "test-secret-0123456789"


@pytest.fixture
def config_file(tmp_path) -> Path:
    dest = tmp_path / "projects.yaml"
    shutil.copy(EXAMPLE, dest)
    return dest


@pytest.fixture
def settings(tmp_path, config_file) -> Settings:
    os.environ.pop("NXSPRINT_MODE", None)
    url = f"sqlite:///{tmp_path / 'test.db'}"
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")
    return Settings(
        mode=Mode.DRY_RUN,
        api_secret=SECRET,
        database_url=url,
        config_path=str(config_file),
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
