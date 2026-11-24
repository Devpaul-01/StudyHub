"""
Shared time helpers.

All "what day is it?" decisions for activity tracking (streaks, daily
activity rows, heatmaps, view counts) must use the same clock. Timestamps
in the database are stored as naive UTC (``datetime.utcnow()``), so a day
boundary has to be a UTC boundary as well. ``datetime.date.today()`` uses
the *server's local timezone*, which silently disagrees with stored
timestamps whenever the host is not running in UTC.
"""
import datetime


def utc_now() -> datetime.datetime:
    """Naive UTC datetime, matching how timestamps are stored."""
    return datetime.datetime.utcnow()


def utc_today() -> datetime.date:
    """Today's date in UTC."""
    return utc_now().date()
