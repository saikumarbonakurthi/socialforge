from datetime import UTC, date, datetime, timedelta

import pytest

from app.config import load_config
from app.domain.calendar import (
    can_message_now,
    is_working_day,
    working_days_between,
    working_days_in_range,
)
from app.settings import Mode


@pytest.fixture
def cfg(config_file):
    return load_config(config_file, Mode.DRY_RUN).projects[0]


def ist(y, m, d, h, mi=0):
    """A wall clock time in Asia/Kolkata (UTC+5:30) as an aware datetime."""
    return datetime(y, m, d, h, mi, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def test_working_day_weekend_and_holiday(cfg):
    assert is_working_day(date(2026, 10, 1), cfg)  # Thursday
    assert not is_working_day(date(2026, 10, 3), cfg)  # Saturday
    cfg2 = cfg.model_copy(update={"holidays": [date(2026, 10, 1)]})
    assert not is_working_day(date(2026, 10, 1), cfg2)


def test_working_days_between_skips_weekend_and_holidays(cfg):
    thu, mon = ist(2026, 10, 1, 11), ist(2026, 10, 5, 11)
    assert working_days_between(thu, mon, cfg) == 2  # Fri and Mon; Sat and Sun skipped
    assert working_days_between(mon, thu, cfg) == 0
    holiday = cfg.model_copy(update={"holidays": [date(2026, 10, 2)]})
    assert working_days_between(thu, mon, holiday) == 1


def test_working_days_between_uses_project_timezone(cfg):
    # 23:00 UTC on Thursday is already Friday morning in Kolkata.
    a = datetime(2026, 10, 1, 23, 0, tzinfo=UTC)
    b = datetime(2026, 10, 2, 5, 0, tzinfo=UTC)
    assert working_days_between(a, b, cfg) == 0


def test_working_days_in_range(cfg):
    assert working_days_in_range(date(2026, 9, 21), date(2026, 10, 5), cfg) == 10
    assert working_days_in_range(date(2026, 10, 5), date(2026, 10, 5), cfg) == 0
    assert working_days_in_range(date(2026, 10, 6), date(2026, 10, 5), cfg) == 0


@pytest.mark.parametrize(
    "when,tz,ok",
    [
        (ist(2026, 10, 1, 11), "Asia/Kolkata", True),
        (ist(2026, 10, 1, 9, 0), "Asia/Kolkata", False),  # before 09:30 working hours
        (ist(2026, 10, 1, 18, 45), "Asia/Kolkata", False),  # after 18:30
        (ist(2026, 10, 1, 20, 0), "Asia/Kolkata", False),  # quiet hours, wraps midnight
        (ist(2026, 10, 3, 11), "Asia/Kolkata", False),  # Saturday
        (ist(2026, 10, 1, 11), "America/New_York", False),  # 01:30 for them
    ],
)
def test_can_message_now(cfg, when, tz, ok):
    assert can_message_now(when, tz, cfg) is ok
