"""Slot generation and conflict detection.

`appointments.scheduled_at` is TIMESTAMPTZ, so a naive datetime handed to
Postgres is silently read as UTC — that is how a 10:00 booking ends up
displaying as 12:00. Every datetime here is therefore made timezone-aware in
the clinic's zone before it crosses the DB boundary, and every comparison is
done between instants rather than between ISO strings.
"""
import re
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.database import get_db, execute_with_retry

SLOT_STEP_MINUTES = 30
# How far ahead of "now" a slot has to start before it is still worth offering
# to someone booking themselves in.
MIN_BOOKING_LEAD_MINUTES = 15
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


def clinic_today() -> date:
    """Today as the clinic reckons it — server UTC can already be tomorrow."""
    return datetime.now(clinic_tz()).date()


def clinic_now() -> datetime:
    """The clinic's current wall-clock instant."""
    return datetime.now(clinic_tz())


def earliest_bookable_instant() -> datetime:
    """The first moment a self-service booking may still start.

    A slot that has not started yet is technically bookable, but offering
    10:00 at 09:58 wastes the visitor's time — they still have to get here.
    """
    return clinic_now() + timedelta(minutes=MIN_BOOKING_LEAD_MINUTES)


def drop_past_slots(slots: List[str]) -> List[str]:
    """`get_available_slots` output, minus the slots that have already gone.

    `get_available_slots` answers "does this doctor work this slot?", which is
    a property of the schedule and stays true for a morning that has already
    happened — that is what the staff-side form wants when a receptionist
    records a walk-in from earlier today. "Can this visitor still book it?" is
    a different question, so the surfaces that serve visitors filter here
    rather than the shared generator changing meaning underneath them.

    Slot strings carry an offset, so `fromisoformat` yields an aware datetime
    and the comparison is between instants, never between spellings.
    """
    cutoff = earliest_bookable_instant()
    return [s for s in slots if datetime.fromisoformat(s) >= cutoff]


_RELATIVE_DAYS = {"today": 0, "tomorrow": 1, "day after tomorrow": 2}


def parse_clinic_datetime(date_str: str, time_str: str) -> datetime:
    """Build a naive clinic wall-clock datetime from model-supplied text.

    Prompts ask for `YYYY-MM-DD` and `HH:MM`, but the model still returns
    "tomorrow" and "10am" often enough that a strict `fromisoformat` on the
    concatenation fails the whole booking. Normalize the common spellings
    instead; anything genuinely unparseable raises ValueError for the caller
    to report back so it can try again.
    """
    raw_date = (date_str or "").strip().lower()
    raw_time = (time_str or "09:00").strip().lower()

    if raw_date in _RELATIVE_DAYS:
        day = clinic_today() + timedelta(days=_RELATIVE_DAYS[raw_date])
    else:
        day = date.fromisoformat(raw_date)  # raises on anything else

    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw_time)
    if not m:
        raise ValueError(f"Unrecognized time: {time_str!r}")
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Out-of-range time: {time_str!r}")

    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


def parse_when(value: str) -> datetime:
    """Parse a single datetime string an agent supplied, however it spelled it.

    Agents are told to send ISO `YYYY-MM-DDTHH:MM`, and mostly do; the fallback
    splits "2026-08-03 10am" / "tomorrow 14:00" into the two-part form.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("No date/time given")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    parts = raw.replace("T", " ").split()
    if len(parts) == 1:
        return parse_clinic_datetime(parts[0], "09:00")
    # "day after tomorrow 10:00" — everything but the last token is the date.
    return parse_clinic_datetime(" ".join(parts[:-1]), parts[-1])


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


def complete_elapsed_appointments() -> int:
    """Promote `confirmed` appointments whose end time has passed to `completed`.

    There is no scheduler in this prototype, so the sweep runs lazily off the
    appointment list — the one read path that every surface showing a status
    goes through. One indexed query finds candidates and one update settles
    them, so a load with nothing to do costs a single query.

    Two limits are deliberate:

    - **Only `confirmed` is promoted.** A `proposed` appointment whose time
      passed was never accepted by anyone, so calling it "completed" would
      assert the visit happened on no evidence; it stays proposed and stale,
      which is the honest state and what the booking-requests queue is for.
      `cancelled` is terminal. This mirrors `VALID_TRANSITIONS` exactly, so the
      sweep can never make a move a human couldn't.
    - **The end of the appointment, not its start.** Comparing `scheduled_at`
      alone marks an appointment completed while the patient is still in the
      room.
    """
    db = get_db()
    now = datetime.now(clinic_tz())

    # `scheduled_at < now` is a cheap, indexed over-approximation: an
    # appointment that hasn't started certainly hasn't ended. The exact end is
    # then computed per row, because `scheduled_at + duration_min` is not
    # something postgrest can filter on.
    candidates = execute_with_retry(
        db.table("appointments")
        .select("id,scheduled_at,duration_min")
        .eq("status", "confirmed")
        .lt("scheduled_at", now.isoformat())
    ).data or []

    elapsed = [
        row["id"] for row in candidates
        if to_clinic(datetime.fromisoformat(row["scheduled_at"]))
        + timedelta(minutes=row.get("duration_min") or SLOT_STEP_MINUTES) <= now
    ]
    if not elapsed:
        return 0

    execute_with_retry(
        db.table("appointments").update({"status": "completed"}).in_("id", elapsed)
    )
    return len(elapsed)
