"""
Reporting Agent — fully deterministic, no LLM needed.
Queries DB, formats data, returns structured report payload.
"""
from __future__ import annotations
from datetime import date
from typing import Any, Dict
from app.services.report_service import (
    get_appointment_summary,
    get_no_show_report,
    get_missing_documents_report,
    build_pdf_report,
    build_csv_report,
)


def tool_appointment_summary(date_from: str, date_to: str) -> Dict[str, Any]:
    return get_appointment_summary(date.fromisoformat(date_from), date.fromisoformat(date_to))


def tool_no_show_report(date_from: str, date_to: str) -> Dict[str, Any]:
    return get_no_show_report(date.fromisoformat(date_from), date.fromisoformat(date_to))


def tool_missing_documents_report() -> Dict[str, Any]:
    return get_missing_documents_report()


def tool_export_pdf(title: str, sections: list) -> bytes:
    return build_pdf_report(title, sections)


def tool_export_csv(headers: list, rows: list) -> str:
    return build_csv_report(headers, rows)


async def run_reporting_agent(date_from: str, date_to: str, fmt: str = "pdf") -> Dict[str, Any]:
    summary = tool_appointment_summary(date_from, date_to)
    no_shows = tool_no_show_report(date_from, date_to)
    missing = tool_missing_documents_report()
    return {
        "appointment_summary": summary,
        "no_show_report": no_shows,
        "missing_documents": missing,
        "date_from": date_from,
        "date_to": date_to,
        "format": fmt,
    }


REPORTING_TOOLS = {
    "appointment_summary": tool_appointment_summary,
    "no_show_report": tool_no_show_report,
    "missing_documents_report": tool_missing_documents_report,
    "export_pdf": tool_export_pdf,
    "export_csv": tool_export_csv,
}
