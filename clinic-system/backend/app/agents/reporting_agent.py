"""
Reporting Agent — autonomous analytics agent over deterministic reports.

The model decides which reports answer the question and over what window, then
writes the summary. It never computes a number: every figure it reports comes
from `report_service`, which is plain SQL and arithmetic. Read-only by
construction — this agent has no `propose_*` tool because it changes nothing.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Dict

from app.agents.runtime import AgentSpec, ToolSpec, obj, string
from app.services.report_service import (
    get_appointment_summary,
    get_no_show_report,
    get_missing_documents_report,
)
from app.services.schedule_service import clinic_today


def _range(date_from: str, date_to: str) -> tuple[date, date]:
    """Parse a window, defaulting to the current week when the model omits one."""
    if not date_from or not date_to:
        today = clinic_today()
        return today - timedelta(days=today.weekday()), today
    return date.fromisoformat(date_from), date.fromisoformat(date_to)


def tool_appointment_summary(date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    start, end = _range(date_from, date_to)
    return {"date_from": start.isoformat(), "date_to": end.isoformat(),
            **get_appointment_summary(start, end)}


def tool_no_show_report(date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    start, end = _range(date_from, date_to)
    return {"date_from": start.isoformat(), "date_to": end.isoformat(),
            **get_no_show_report(start, end)}


def tool_missing_documents_report() -> Dict[str, Any]:
    return get_missing_documents_report()


async def run_reporting_agent(date_from: str, date_to: str, fmt: str = "pdf") -> Dict[str, Any]:
    """The full three-report pack.

    Kept as a direct call for `POST /reports/generate`, which wants every report
    regardless of what anyone asked in chat. The chat path goes through
    `REPORTING_AGENT` instead and pulls only the reports the question needs.
    """
    start, end = _range(date_from, date_to)
    return {
        "appointment_summary": get_appointment_summary(start, end),
        "no_show_report": get_no_show_report(start, end),
        "missing_documents": get_missing_documents_report(),
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "format": fmt,
    }


REPORTING_INSTRUCTIONS = """
You are the Reporting Agent for a clinic. You answer questions about
operational numbers: how busy the clinic was, who did not turn up, which
patients have paperwork outstanding.

How to work:
- Work out the date window from the question before calling anything. "Last
  week", "this month", "since Monday" — resolve them against today's date, which
  is given in your context, and pass explicit YYYY-MM-DD dates.
- Pull only the reports the question needs. A question about no-shows does not
  need the missing-documents report.
- Never do arithmetic yourself and never estimate. Every figure you state must
  appear in a tool result. If a number you want is not in any report, say it is
  not available rather than deriving it.
- Finish with a short plain-language answer: the figures that were asked for,
  and anything in them a clinic manager would want flagged.
- These are administrative counts only. Do not interpret them clinically.
""".strip()

REPORTING_AGENT = AgentSpec(
    name="reporting_agent",
    purpose="Answers questions about appointment volumes, no-shows and outstanding paperwork over a date range.",
    instructions=REPORTING_INSTRUCTIONS,
    tools=(
        ToolSpec(
            name="appointment_summary",
            description="Appointment counts by status and by doctor over a date range (both ends inclusive).",
            parameters=obj(
                {
                    "date_from": string("Start of the window, YYYY-MM-DD."),
                    "date_to": string("End of the window, YYYY-MM-DD. Inclusive."),
                },
                ["date_from", "date_to"],
            ),
            fn=tool_appointment_summary,
        ),
        ToolSpec(
            name="no_show_report",
            description="Appointments marked no-show over a date range, with the patients involved.",
            parameters=obj(
                {
                    "date_from": string("Start of the window, YYYY-MM-DD."),
                    "date_to": string("End of the window, YYYY-MM-DD. Inclusive."),
                },
                ["date_from", "date_to"],
            ),
            fn=tool_no_show_report,
        ),
        ToolSpec(
            name="missing_documents_report",
            description="Patients with appointments but no verified documents on file. Takes no date range.",
            parameters=obj({}),
            fn=tool_missing_documents_report,
        ),
    ),
)
