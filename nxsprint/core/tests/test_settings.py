import pytest

from app.settings import Settings

SECRET = "0123456789abcdef"


def test_blank_optional_values_mean_unset(monkeypatch):
    for name in ("MAX_DAILY_USD", "MODEL", "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET"):
        monkeypatch.setenv(f"NXSPRINT_{name}", "")
    monkeypatch.setenv("NXSPRINT_API_SECRET", SECRET)
    s = Settings(_env_file=None)
    assert s.max_daily_usd is None and s.llm_model is None and s.github_token is None


def test_blank_api_secret_still_fails_loudly(monkeypatch):
    monkeypatch.setenv("NXSPRINT_API_SECRET", "")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_env_file_in_parent_dir_is_found(tmp_path, monkeypatch):
    (tmp_path / "core").mkdir()
    (tmp_path / ".env").write_text(f"NXSPRINT_API_SECRET={SECRET}\nNXSPRINT_MAX_DAILY_USD=\n")
    monkeypatch.delenv("NXSPRINT_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path / "core")
    assert Settings().api_secret == SECRET
