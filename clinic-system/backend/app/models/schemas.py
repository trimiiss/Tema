from __future__ import annotations
from typing import Any, List, Optional
from pydantic import BaseModel, EmailStr
from datetime import date, datetime, time


# ---- Patients ----
class PatientCreate(BaseModel):
    code: str
    first_name: str
    last_name: str
    dob: Optional[date] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[date] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None


class PatientOut(BaseModel):
    id: str
    code: str
    first_name: str
    last_name: str
    dob: Optional[date]
    gender: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    address: Optional[str]
    notes: Optional[str]
    created_at: Optional[datetime]


# ---- Appointments ----
class AppointmentCreate(BaseModel):
    patient_id: str
    staff_id: str
    service_id: Optional[str] = None
    scheduled_at: datetime
    duration_min: int = 30
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    patient_id: Optional[str] = None
    staff_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    duration_min: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    service_id: Optional[str] = None


class AppointmentOut(BaseModel):
    id: str
    patient_id: str
    staff_id: str
    service_id: Optional[str]
    scheduled_at: datetime
    duration_min: int
    status: str
    notes: Optional[str]
    created_at: Optional[datetime]
    patient_name: Optional[str] = None
    staff_name: Optional[str] = None
    service_name: Optional[str] = None
    # 'staff' | 'patient_portal' — lets the Booking Requests queue tell a
    # visitor's self-service submission apart from one a receptionist entered,
    # since both start life with the same status ('proposed').
    source: Optional[str] = "staff"
    patient_phone: Optional[str] = None
    patient_email: Optional[str] = None


# ---- Staff ----
class StaffCreate(BaseModel):
    full_name: str
    role: str = "doctor"            # "doctor" | "receptionist"
    specialty: Optional[str] = None
    bio: Optional[str] = None
    # Optional login account — when both are given a Supabase Auth user is
    # created and the role is assigned via user_roles.
    email: Optional[str] = None
    password: Optional[str] = None
    # Weekly working hours, only meaningful for doctors (0=Mon … 6=Sun).
    work_days: List[int] = []
    start_time: Optional[time] = None
    end_time: Optional[time] = None


class StaffUpdate(BaseModel):
    full_name: Optional[str] = None
    specialty: Optional[str] = None
    bio: Optional[str] = None
    active: Optional[bool] = None


class StaffOut(BaseModel):
    id: str
    user_id: Optional[str] = None
    full_name: str
    specialty: Optional[str] = None
    bio: Optional[str] = None
    active: Optional[bool] = True


class ScheduleEntry(BaseModel):
    weekday: int
    start_time: time
    end_time: time


class ScheduleOut(ScheduleEntry):
    id: str
    staff_id: str


class ServiceOut(BaseModel):
    id: str
    name: str
    duration_minutes: int
    description: Optional[str] = None


# ---- Documents ----
class DocumentOut(BaseModel):
    id: str
    patient_id: Optional[str]
    filename: str
    doc_type: Optional[str]
    status: str
    created_at: Optional[datetime]


class DocumentFieldOut(BaseModel):
    id: str
    document_id: str
    field_name: str
    field_value: Optional[str]
    confidence: Optional[float]
    verified_by: Optional[str]


# ---- Agent runs ----
class AgentRunRequest(BaseModel):
    input_text: str


class AgentRunOut(BaseModel):
    id: str
    user_id: Optional[str]
    input_text: str
    intent: Optional[str]
    status: str
    result: Optional[Any]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
    steps: List[dict] = []
    gates: List[dict] = []


class ApprovalDecision(BaseModel):
    decision: str  # "approved" | "rejected"


# ---- Reports ----
class ReportRequest(BaseModel):
    date_from: date
    date_to: date
    format: str = "pdf"  # "pdf" | "csv"


# ---- Public booking (unauthenticated) ----
# The visitor has no JWT, so `session_id` — a UUID the browser generates and
# keeps in localStorage — is the only thing identifying "their" runs and gates.
# Every public endpoint that reads or decides a run/gate checks it against
# `agent_runs.session_id`.
class PublicChatRequest(BaseModel):
    session_id: str
    message: str


class PublicConfirmRequest(BaseModel):
    session_id: str
    decision: str  # "approved" | "rejected"


class PublicDoctorOut(BaseModel):
    id: str
    full_name: str
    specialty: Optional[str] = None
    bio: Optional[str] = None


class PublicReasonOut(BaseModel):
    reason: str
    label: str
    specialty: str


class PublicSlotsOut(BaseModel):
    staff_id: str
    date: str
    slots: List[str]
