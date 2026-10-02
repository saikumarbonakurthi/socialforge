"""Working time helpers. Pure functions, clock is always passed in."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import ProjectCfg, Window


def is_working_day(d: date, cfg: ProjectCfg) -> bool:
    return d.isoweekday() in cfg.working_days and d not in cfg.holidays


def working_days_between(start: datetime, end: datetime, cfg: ProjectCfg) -> int:
    """Working days elapsed after `start`'s date up to and including `end`'s date.

    Update on Monday, now Wednesday: Tuesday and Wednesday, so 2. Dates are in the project timezone.
    """
    tz = ZoneInfo(cfg.timezone)
    a, b = start.astimezone(tz).date(), end.astimezone(tz).date()
    return sum(is_working_day(a + timedelta(days=i), cfg) for i in range(1, (b - a).days + 1))


def working_days_in_range(start: date, end: date, cfg: ProjectCfg) -> int:
    """Working days d with start <= d < end."""
    return sum(is_working_day(start + timedelta(days=i), cfg) for i in range(max(0, (end - start).days)))


def _in_window(t: time, w: Window) -> bool:
    if w.start <= w.end:
        return w.start <= t < w.end
    return t >= w.start or t < w.end  # wraps midnight, e.g. 19:00 to 09:00


def can_message_now(now: datetime, member_tz: str, cfg: ProjectCfg) -> bool:
    """True on a working day, inside working hours, outside quiet hours, in the member's timezone."""
    local = now.astimezone(ZoneInfo(member_tz))
    return (
        is_working_day(local.date(), cfg)
        and _in_window(local.time(), cfg.working_hours)
        and not _in_window(local.time(), cfg.quiet_hours)
    )
