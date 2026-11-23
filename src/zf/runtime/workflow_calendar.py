"""Five-field numeric cron schedules, evaluated as civil time in an IANA zone."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _values(field: str, low: int, high: int) -> tuple[int, ...]:
    values = set()
    for part in field.split(","):
        bounds, sep, step_text = part.partition("/")
        if sep and (
            not step_text.isascii() or not step_text.isdigit() or int(step_text) < 1
        ):
            raise ValueError("invalid cron step")
        step = int(step_text) if sep else 1
        if bounds == "*":
            start, end = low, high
        else:
            first, dash, last = bounds.partition("-")
            if (
                not first.isascii()
                or not first.isdigit()
                or (dash and not last.isdigit())
            ):
                raise ValueError("invalid cron field")
            start = int(first)
            end = int(last) if dash else high if sep else start
        if not low <= start <= end <= high:
            raise ValueError("cron field outside its range")
        values.update(range(start, end + 1, step))
    return tuple(sorted(values))


class WorkflowCalendar:
    def __init__(self, expression: str, zone: str):
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron requires minute hour day month weekday")
        try:
            self.zone = ZoneInfo(zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        self.minutes, self.hours, self.days, self.months, weekdays = (
            _values(value, lo, hi)
            for value, (lo, hi) in zip(
                fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)], strict=True
            )
        )
        self.weekdays = {day % 7 for day in weekdays}
        self.either_day = fields[2] != "*" and fields[4] != "*"

    def _matches_day(self, day: date) -> bool:
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays
        return day.month in self.months and (
            (dom or dow) if self.either_day else (dom and dow)
        )

    def due(
        self, after: datetime, through: datetime, *, limit: int = 1
    ) -> list[datetime]:
        """Most recent bounded occurrences in (after, through], in chronological order."""
        if (
            after.tzinfo is None
            or through.tzinfo is None
            or type(limit) is not int
            or limit < 1
        ):
            raise ValueError(
                "cron requires an aware window and positive occurrence limit"
            )
        start, end = after.astimezone(timezone.utc), through.astimezone(timezone.utc)
        if end <= start:
            return []
        day, earliest = (
            end.astimezone(self.zone).date(),
            start.astimezone(self.zone).date(),
        )
        found = []
        while day >= earliest:
            if day.month not in self.months:
                day = day.replace(day=1) - timedelta(days=1)
                continue
            if self._matches_day(day):
                for hour in reversed(self.hours):
                    for minute in reversed(self.minutes):
                        local = datetime.combine(day, time(hour, minute), self.zone)
                        utc = local.astimezone(timezone.utc)
                        # fold=0 chooses the earlier occurrence; round trip excludes imaginary times.
                        if start < utc <= end and utc.astimezone(self.zone).replace(
                            tzinfo=None
                        ) == local.replace(tzinfo=None):
                            found.append(utc)
                            if len(found) == limit:
                                return list(reversed(found))
            day -= timedelta(days=1)
        return list(reversed(found))
