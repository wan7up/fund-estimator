from __future__ import annotations

from datetime import date, timedelta

from chinese_calendar import is_workday


class TradingCalendarUnavailable(Exception):
    pass


def is_trading_day(value: date) -> bool:
    """Return whether the mainland A-share market can trade on this date.

    Chinese statutory calendars include make-up work Saturdays and Sundays.
    The stock exchanges remain closed on weekends, so weekends are excluded
    explicitly before consulting the holiday calendar.
    """

    if value.weekday() >= 5:
        return False
    try:
        return bool(is_workday(value))
    except NotImplementedError as exc:
        # The bundled calendar is finite. Fail explicitly rather than treating
        # an unknown future weekday as either open or closed.
        raise TradingCalendarUnavailable from exc


def previous_trading_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate
