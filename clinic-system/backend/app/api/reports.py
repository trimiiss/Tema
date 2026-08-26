from datetime import date
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from app.core.auth import require_roles
from app.core.audit import log_action
from app.models.schemas import ReportRequest
from app.services.report_service import (
    get_appointment_summary,
    get_no_show_report,
    get_missing_documents_report,
    build_pdf_report,
    build_csv_report,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary")
def report_summary(
    date_from: date,
    date_to: date,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    summary = get_appointment_summary(date_from, date_to)
    no_shows = get_no_show_report(date_from, date_to)
    missing_docs = get_missing_documents_report()
    return {
        "total_appointments": summary["total"],
        "by_status": summary["by_status"],
        "no_show_count": no_shows["count"],
        "missing_documents_count": missing_docs["count"],
    }


@router.post("/generate")
def generate_report(
    body: ReportRequest,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    summary = get_appointment_summary(body.date_from, body.date_to)
    no_shows = get_no_show_report(body.date_from, body.date_to)
    missing_docs = get_missing_documents_report()

    date_label = f"{body.date_from} to {body.date_to}"
    log_action(
        user["id"], "generate", "report", None,
        {"date_from": str(body.date_from), "date_to": str(body.date_to), "format": body.format},
    )

    if body.format == "csv":
        rows = [[r["status"], r["scheduled_at"], r.get("staff_id", ""), r.get("patient_id", "")]
                for r in summary["rows"]]
        csv_data = build_csv_report(["Status", "Scheduled At", "Staff ID", "Patient ID"], rows)
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="report_{body.date_from}.csv"'},
        )

    # Build PDF
    by_status = summary["by_status"]
    status_table = [["Status", "Count"]] + [[k, str(v)] for k, v in by_status.items()]

    ns_rows = no_shows["rows"]
    ns_table = [["Patient", "Scheduled At", "Status"]] + [
        [
            f"{r.get('patients', {}).get('first_name', '')} {r.get('patients', {}).get('last_name', '')}".strip() or r.get("patient_id", ""),
            str(r["scheduled_at"])[:16],
            r["status"],
        ]
        for r in ns_rows
    ]

    md_rows = missing_docs["patients"]
    md_table = [["Code", "Patient Name", "Missing Doc Types"]] + [
        [p["code"], f"{p['first_name']} {p['last_name']}", ", ".join(p["missing_doc_types"])]
        for p in md_rows
    ]

    sections = [
        {"heading": f"Appointment Summary ({date_label})", "table": status_table},
        {"heading": f"No-Shows & Cancellations ({date_label})", "table": ns_table if len(ns_table) > 1 else [["No records"]]},
        {"heading": "Patients with Missing Documents", "table": md_table if len(md_table) > 1 else [["No records"]]},
    ]

    pdf = build_pdf_report(f"Weekly Operational Report — {date_label}", sections)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{body.date_from}.pdf"'},
    )


@router.get("/audit-log")
def get_audit_log(
    entity_type: str = "",
    limit: int = 100,
    user: dict = Depends(require_roles("admin")),
):
    from app.core.database import get_db
    db = get_db()
    q = db.table("audit_logs").select("*")
    if entity_type:
        q = q.eq("entity_type", entity_type)
    resp = q.order("timestamp", desc=True).limit(limit).execute()
    return resp.data
