import csv
import io
from datetime import date
from typing import Any, Dict
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from app.core.database import get_db
from app.services.schedule_service import to_clinic
from datetime import datetime, time, timedelta


def _day_bounds(date_from: date, date_to: date) -> tuple[str, str]:
    """Clinic-local [start, end) instants covering both dates inclusively.

    `date_to` is turned into the *following* midnight: comparing against
    `date_to` itself resolves to 00:00 on that day and silently drops
    everything scheduled during the last day of the range.
    """
    start = to_clinic(datetime.combine(date_from, time.min))
    end = to_clinic(datetime.combine(date_to + timedelta(days=1), time.min))
    return start.isoformat(), end.isoformat()


def get_appointment_summary(date_from: date, date_to: date) -> Dict[str, Any]:
    db = get_db()
    start, end = _day_bounds(date_from, date_to)
    resp = (
        db.table("appointments")
        .select("status, staff_id, service_id, scheduled_at")
        .gte("scheduled_at", start)
        .lt("scheduled_at", end)
        .order("scheduled_at")
        .execute()
    )
    rows = resp.data
    total = len(rows)
    by_status: Dict[str, int] = {}
    for r in rows:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1
    return {"total": total, "by_status": by_status, "rows": rows}


def get_no_show_report(date_from: date, date_to: date) -> Dict[str, Any]:
    db = get_db()
    start, end = _day_bounds(date_from, date_to)
    resp = (
        db.table("appointments")
        .select("*, patients(first_name,last_name,code), staff(full_name)")
        .in_("status", ["no_show", "cancelled"])
        .gte("scheduled_at", start)
        .lt("scheduled_at", end)
        .order("scheduled_at")
        .execute()
    )
    return {"count": len(resp.data), "rows": resp.data}


def get_missing_documents_report() -> Dict[str, Any]:
    db = get_db()
    patients = db.table("patients").select("id,code,first_name,last_name").execute().data
    docs = db.table("documents").select("patient_id,doc_type,status").execute().data
    docs_by_patient = {}
    for d in docs:
        pid = d["patient_id"]
        if pid:
            docs_by_patient.setdefault(pid, []).append(d)

    missing = []
    for p in patients:
        p_docs = docs_by_patient.get(p["id"], [])
        verified_types = {d["doc_type"] for d in p_docs if d["status"] == "verified"}
        needed = {"referral", "insurance", "id"} - verified_types
        if needed:
            missing.append({**p, "missing_doc_types": list(needed)})
    return {"count": len(missing), "patients": missing}


def build_pdf_report(title: str, sections: list[Dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=50, bottomMargin=40)
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Title"]), Spacer(1, 16)]

    for section in sections:
        elements.append(Paragraph(section["heading"], styles["Heading2"]))
        elements.append(Spacer(1, 8))
        if section.get("table"):
            t = Table(section["table"], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
                ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE",   (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
            ]))
            elements.append(t)
        elements.append(Spacer(1, 16))

    doc.build(elements)
    return buf.getvalue()


def build_csv_report(headers: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()
