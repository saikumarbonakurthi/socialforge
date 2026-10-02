import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    return Settings(
        mode=Mode.DRY_RUN,
        api_secret=SECRET,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        config_path=str(config_file),
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
