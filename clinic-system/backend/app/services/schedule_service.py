"""Slot generation and conflict detection.

`appointments.scheduled_at` is TIMESTAMPTZ, so a naive datetime handed to
Postgres is silently read as UTC — that is how a 10:00 booking ends up
displaying as 12:00. Every datetime here is therefore made timezone-aware in
the clinic's zone before it crosses the DB boundary, and every comparison is
done between instants rather than between ISO strings.
"""
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.database import get_db, execute_with_retry

SLOT_STEP_MINUTES = 30
# Widest window we look back for an appointment that could still be running
# when a new one starts.
MAX_APPOINTMENT_HOURS = 12


def clinic_tz() -> ZoneInfo:
    return ZoneInfo(settings.clinic_timezone)


def to_clinic(dt: datetime) -> datetime:
    """Interpret a naive datetime as clinic wall-clock; convert an aware one."""
    tz = clinic_tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _parse_hm(value: str) -> tuple[int, int]:
    h, m = value[:5].split(":")
    return int(h), int(m)


def default_schedule_rows(staff_id: str) -> List[dict]:
    """Clinic-wide fallback hours (Mon–Fri 09:00–17:00 out of the box)."""
    days = [int(d) for d in settings.default_work_days.split(",") if d.strip()]
    return [
        {
            "staff_id": staff_id,
            "weekday": d,
            "start_time": settings.default_work_start,
            "end_time": settings.default_work_end,
        }
        for d in days
    ]


def _schedules_for(staff_id: str, weekday: int) -> List[dict]:
    """Working blocks for a weekday, falling back to clinic defaults.

    The fallback applies only when a doctor has *no* schedule rows at all —
    otherwise a doctor deliberately set to Tue/Thu would sprout Mon/Wed/Fri
    hours they never agreed to.
    """
    db = get_db()
    resp = execute_with_retry(db.table("schedules").select("*").eq("staff_id", staff_id))
    rows = resp.data or []
    if not rows:
        rows = default_schedule_rows(staff_id)
    return [r for r in rows if r["weekday"] == weekday]


def _booked_intervals(staff_id: str, window_start: datetime, window_end: datetime,
                      exclude_id: Optional[str] = None) -> List[tuple[datetime, datetime, dict]]:
    """Live appointments for `staff_id` overlapping the window, as instants."""
    db = get_db()
    resp = execute_with_retry(
        db.table("appointments")
        .select("*")
        .eq("staff_id", staff_id)
        .not_.in_("status", ["cancelled", "no_show"])
        .gte("scheduled_at", window_start.isoformat())
        .lt("scheduled_at", window_end.isoformat())
    )
    intervals = []
    for row in resp.data or []:
        if exclude_id and row.get("id") == exclude_id:
            continue
        start = to_clinic(datetime.fromisoformat(row["scheduled_at"]))
        end = start + timedelta(minutes=row.get("duration_min") or SLOT_STEP_MINUTES)
        intervals.append((start, end, row))
    return intervals


def get_available_slots(staff_id: str, date: datetime, duration_min: int = SLOT_STEP_MINUTES,
                        exclude_appointment_id: Optional[str] = None) -> List[str]:
    """Open start times for `staff_id` on `date`, as ISO strings with offset.

    A slot is offered only if the whole appointment fits inside a working block
    and overlaps nothing already booked — so a 60-minute service does not get
    slotted into the last 30 minutes of a shift.
    """
    day = to_clinic(date)

    blocks = _schedules_for(staff_id, day.weekday())
    if not blocks:
        return []

    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    booked = _booked_intervals(
        staff_id,
        day_start - timedelta(hours=MAX_APPOINTMENT_HOURS),
        day_end,
        exclude_appointment_id,
    )

    duration = timedelta(minutes=duration_min or SLOT_STEP_MINUTES)
    slots: List[str] = []
    for s in blocks:
        start_h, start_m = _parse_hm(s["start_time"])
        end_h, end_m = _parse_hm(s["end_time"])
        current = day_start.replace(hour=start_h, minute=start_m)
        shift_end = day_start.replace(hour=end_h, minute=end_m)

        while current + duration <= shift_end:
            slot_end = current + duration
            if not any(start < slot_end and end > current for start, end, _ in booked):
                slots.append(current.isoformat())
            current += timedelta(minutes=SLOT_STEP_MINUTES)
    return sorted(set(slots))


def check_conflict(staff_id: str, scheduled_at: datetime, duration_min: int = SLOT_STEP_MINUTES,
                   exclude_appointment_id: Optional[str] = None) -> Optional[dict]:
    """The first appointment overlapping [scheduled_at, +duration), if any.

    Overlap is computed on both endpoints, so an existing long appointment is
    caught even when the new one starts after it — a bare `scheduled_at` range
    query misses that case entirely.
    """
    start = to_clinic(scheduled_at)
    end = start + timedelta(minutes=duration_min or SLOT_STEP_MINUTES)

    booked = _booked_intervals(
        staff_id,
        start - timedelta(hours=MAX_APPOINTMENT_HOURS),
        end,
        exclude_appointment_id,
    )
    for b_start, b_end, row in booked:
        if b_start < end and b_end > start:
            return row
    return None
