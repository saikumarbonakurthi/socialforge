import pytest
import yaml

from app.config import ConfigError, load_config
from app.settings import Mode


def _edit(path, fn):
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data))


def test_example_loads_in_dry_run(config_file):
    cfg = load_config(config_file, Mode.DRY_RUN)
    assert cfg.placeholder and cfg.projects[0].members[0].role.value == "lead"


def test_placeholder_refused_in_live(config_file):
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(config_file, Mode.LIVE)


def test_live_ok_when_not_placeholder(config_file):
    _edit(config_file, lambda d: d.update(placeholder=False))
    assert load_config(config_file, Mode.LIVE)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda d: d["projects"][0].pop("holidays"), "holidays"),
        (lambda d: d["projects"][0].pop("quiet_hours"), "quiet_hours"),
        (
            lambda d: d["projects"][0]["thresholds"].pop("pr_review_wait_hours"),
            "pr_review_wait_hours",
        ),
        (lambda d: d["projects"][0]["cooldowns"]["hours"].pop("NO_ESTIMATE"), "NO_ESTIMATE"),
        (lambda d: d["projects"][0].update(timezone="Mars/Base"), "timezone"),
        (lambda d: d["projects"][0]["members"][0].update(role="member"), "exactly one member"),
        (lambda d: d["projects"][0]["members"][1].update(github_login="asha-demo"), "duplicate"),
        (lambda d: d["projects"][0].update(working_days=[0, 9]), "working_days"),
        (lambda d: d["projects"][0].update(surprise=1), "surprise"),
    ],
)
def test_bad_config_fails_loudly(config_file, mutate, match):
    _edit(config_file, mutate)
    with pytest.raises(ConfigError, match=match):
        load_config(config_file, Mode.DRY_RUN)


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nope.yaml", Mode.DRY_RUN)
