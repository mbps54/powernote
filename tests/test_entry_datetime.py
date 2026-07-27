from datetime import datetime
from zoneinfo import ZoneInfo

from powernote.entry_datetime import explicit_entry_datetime


NOW = datetime(2026, 7, 26, 14, 21, 12, tzinfo=ZoneInfo("Europe/Berlin"))


def test_uses_yesterday_and_explicit_time() -> None:
    result = explicit_entry_datetime("Вчера ел суп в 19:30", NOW)

    assert result == datetime(2026, 7, 25, 19, 30, tzinfo=NOW.tzinfo)


def test_uses_relative_date_and_message_time_without_explicit_time() -> None:
    result = explicit_entry_datetime("Позавчера занимался спортом", NOW)

    assert result == datetime(2026, 7, 24, 14, 21, 12, tzinfo=NOW.tzinfo)


def test_uses_numeric_date_and_time() -> None:
    result = explicit_entry_datetime("20.07.2026 в 08:10 ел кашу", NOW)

    assert result == datetime(2026, 7, 20, 8, 10, tzinfo=NOW.tzinfo)


def test_uses_russian_month_name() -> None:
    result = explicit_entry_datetime("25 июля вечером гулял в 8 вечера", NOW)

    assert result == datetime(2026, 7, 25, 20, 0, tzinfo=NOW.tzinfo)


def test_returns_none_without_explicit_date_or_time() -> None:
    assert explicit_entry_datetime("Ел овсяную кашу и яблоко", NOW) is None
