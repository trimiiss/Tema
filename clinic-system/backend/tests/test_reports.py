"""Tests for report generation (PDF/CSV)."""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock
from app.services.report_service import (
    get_appointment_summary,
    get_no_show_report,
    get_missing_documents_report,
    build_csv_report,
    build_pdf_report,
)


from tests.conftest import make_chain


def _make_db(data):
    db = MagicMock()
    db.table.return_value = make_chain(data)
    return db


def test_appointment_summary_totals():
    rows = [
        {"status": "confirmed", "scheduled_at": "2026-07-14T09:00"},
        {"status": "confirmed", "scheduled_at": "2026-07-15T10:00"},
        {"status": "no_show",   "scheduled_at": "2026-07-15T11:00"},
        {"status": "cancelled", "scheduled_at": "2026-07-16T09:00"},
    ]
    db = _make_db(rows)
    with patch("app.services.report_service.get_db", return_value=db):
        result = get_appointment_summary(date(2026, 7, 14), date(2026, 7, 20))
    assert result["total"] == 4
    assert result["by_status"]["confirmed"] == 2
    assert result["by_status"]["no_show"] == 1


def test_no_show_report_filters_correctly():
    rows = [
        {"id": "a1", "status": "no_show", "scheduled_at": "2026-07-14T09:00", "patient_id": "p1"},
        {"id": "a2", "status": "cancelled", "scheduled_at": "2026-07-15T10:00", "patient_id": "p2"},
    ]
    db = _make_db(rows)
    with patch("app.services.report_service.get_db", return_value=db):
        result = get_no_show_report(date(2026, 7, 14), date(2026, 7, 20))
    assert result["count"] == 2


def test_missing_documents_report_identifies_gaps():
    patients = [
        {"id": "p1", "code": "P001", "first_name": "Alban", "last_name": "Krasniqi"},
        {"id": "p2", "code": "P002", "first_name": "Fjolla", "last_name": "Berisha"},
    ]
    docs = [
        {"patient_id": "p1", "doc_type": "referral", "status": "verified"},
        {"patient_id": "p1", "doc_type": "insurance", "status": "verified"},
        # p1 missing 'id', p2 missing everything
    ]

    mock_db = MagicMock()
    patient_chain = make_chain(patients)
    doc_chain = make_chain(docs)
    mock_db.table.side_effect = lambda t: patient_chain if t == "patients" else doc_chain

    with patch("app.services.report_service.get_db", return_value=mock_db):
        result = get_missing_documents_report()
    assert result["count"] >= 1


def test_range_includes_the_whole_final_day():
    """`lte(date_to)` resolved to 00:00 and silently dropped the last day."""
    db = _make_db([])
    chain = db.table.return_value
    with patch("app.services.report_service.get_db", return_value=db):
        get_appointment_summary(date(2026, 7, 20), date(2026, 7, 25))

    start = chain.gte.call_args[0][1]
    end = chain.lt.call_args[0][1]
    assert start.startswith("2026-07-20T00:00:00")
    # End is the *next* midnight, so 25 July is fully covered.
    assert end.startswith("2026-07-26T00:00:00")


def test_range_bounds_are_clinic_local_not_utc():
    db = _make_db([])
    chain = db.table.return_value
    with patch("app.services.report_service.get_db", return_value=db):
        get_appointment_summary(date(2026, 7, 20), date(2026, 7, 20))

    start = datetime.fromisoformat(chain.gte.call_args[0][1])
    assert start.utcoffset() == timedelta(hours=2)  # CEST


def test_single_day_range_is_not_empty():
    rows = [{"status": "confirmed", "scheduled_at": "2026-07-20T08:00:00+00:00"}]
    db = _make_db(rows)
    with patch("app.services.report_service.get_db", return_value=db):
        result = get_appointment_summary(date(2026, 7, 20), date(2026, 7, 20))
    assert result["total"] == 1


def test_build_csv_report():
    headers = ["Status", "Count"]
    rows = [["confirmed", "5"], ["cancelled", "2"]]
    csv = build_csv_report(headers, rows)
    assert "Status" in csv
    assert "confirmed" in csv
    assert "5" in csv


def test_build_pdf_report_returns_bytes():
    sections = [
        {"heading": "Test Section", "table": [["Col1", "Col2"], ["A", "B"]]},
    ]
    pdf = build_pdf_report("Test Report", sections)
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"  # PDF magic bytes


def test_empty_report_still_generates():
    sections = [{"heading": "Empty", "table": [["No records"]]}]
    pdf = build_pdf_report("Empty Report", sections)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 100
