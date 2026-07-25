from datetime import datetime, timedelta
from typing import List, Optional
from app.core.database import get_db


def get_available_slots(staff_id: str, date: datetime) -> List[str]:
    db = get_db()
    weekday = date.weekday()  # 0=Mon

    sched = (
        db.table("schedules")
        .select("*")
        .eq("staff_id", staff_id)
        .eq("weekday", weekday)
        .execute()
    )
    if not sched.data:
        return []

    # Existing appointments that day
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    booked = (
        db.table("appointments")
        .select("scheduled_at, duration_min")
        .eq("staff_id", staff_id)
        .gte("scheduled_at", day_start.isoformat())
        .lt("scheduled_at", day_end.isoformat())
        .not_.in_("status", ["cancelled", "no_show"])
        .execute()
    )
    booked_times = {a["scheduled_at"][:16] for a in booked.data}

    slots = []
    for s in sched.data:
        start_h, start_m = map(int, s["start_time"][:5].split(":"))
        end_h,   end_m   = map(int, s["end_time"][:5].split(":"))
        current = date.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end_dt  = date.replace(hour=end_h,   minute=end_m,   second=0, microsecond=0)
        while current < end_dt:
            slot_key = current.isoformat()[:16]
            if slot_key not in booked_times:
                slots.append(current.isoformat())
            current += timedelta(minutes=30)
    return slots


def check_conflict(staff_id: str, scheduled_at: datetime, duration_min: int = 30) -> Optional[dict]:
    db = get_db()
    end_at = scheduled_at + timedelta(minutes=duration_min)

    resp = (
        db.table("appointments")
        .select("*")
        .eq("staff_id", staff_id)
        .not_.in_("status", ["cancelled", "no_show"])
        .gte("scheduled_at", (scheduled_at - timedelta(minutes=duration_min - 1)).isoformat())
        .lt("scheduled_at", end_at.isoformat())
        .execute()
    )
    return resp.data[0] if resp.data else None
