from __future__ import annotations

import re
from datetime import date, datetime, timedelta


_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])[./-](?P<month>0?[1-9]|1[0-2])"
    r"(?:[./-](?P<year>\d{2}|\d{4}))?(?!\d)"
)
_TEXT_DATE_RE = re.compile(
    rf"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])\s+"
    rf"(?P<month>{'|'.join(_MONTHS)})(?:\s+(?P<year>\d{{4}}))?",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"(?:(?:\bв|\bat)\s+)?(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)",
    re.IGNORECASE,
)
_HOUR_RE = re.compile(
    r"(?:\bв|\bat)\s+(?P<hour>[01]?\d|2[0-3])(?:\s*(?:час(?:а|ов)?))?"
    r"\s*(?P<period>утра|дня|вечера|ночи)\b",
    re.IGNORECASE,
)


def _year_for_partial_date(month: int, day: int, reference: datetime) -> int:
    candidate = date(reference.year, month, day)
    if candidate > reference.date() + timedelta(days=1):
        return reference.year - 1
    return reference.year


def explicit_entry_datetime(text: str, fallback: datetime) -> datetime | None:
    normalized = text.casefold().replace("ё", "е")
    target_date: date | None = None

    if re.search(r"\bпозавчера\b|\bday before yesterday\b", normalized):
        target_date = fallback.date() - timedelta(days=2)
    elif re.search(r"\bвчера\b|\byesterday\b", normalized):
        target_date = fallback.date() - timedelta(days=1)
    elif re.search(r"\bсегодня\b|\btoday\b", normalized):
        target_date = fallback.date()
    else:
        numeric_match = _NUMERIC_DATE_RE.search(normalized)
        text_match = _TEXT_DATE_RE.search(normalized)
        if numeric_match:
            day = int(numeric_match.group("day"))
            month = int(numeric_match.group("month"))
            year_text = numeric_match.group("year")
            try:
                year = int(year_text) if year_text else _year_for_partial_date(month, day, fallback)
                if year < 100:
                    year += 2000
                target_date = date(year, month, day)
            except ValueError:
                target_date = None
        elif text_match:
            day = int(text_match.group("day"))
            month = _MONTHS[text_match.group("month").casefold()]
            year_text = text_match.group("year")
            try:
                year = int(year_text) if year_text else _year_for_partial_date(month, day, fallback)
                target_date = date(year, month, day)
            except ValueError:
                target_date = None

    hour: int | None = None
    minute = 0
    time_match = _TIME_RE.search(normalized)
    if time_match:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
    else:
        hour_match = _HOUR_RE.search(normalized)
        if hour_match:
            hour = int(hour_match.group("hour"))
            period = hour_match.group("period")
            if period in ("дня", "вечера") and hour < 12:
                hour += 12
            elif period == "ночи" and hour == 12:
                hour = 0

    if target_date is None and hour is None:
        return None

    target_date = target_date or fallback.date()
    return fallback.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=fallback.hour if hour is None else hour,
        minute=fallback.minute if hour is None else minute,
        second=fallback.second if hour is None else 0,
        microsecond=fallback.microsecond if hour is None else 0,
    )
