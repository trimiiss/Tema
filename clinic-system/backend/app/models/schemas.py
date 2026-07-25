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
    scheduled_at: Optional[datetime] = None
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
