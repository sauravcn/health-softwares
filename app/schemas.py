"""Pydantic v2 schemas for every module."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- agency
class AgencyRead(_Base):
    id: int
    name: str
    geofence_radius_km: float
    late_grace_minutes: int
    early_departure_grace_minutes: int


class AgencyUpdate(_Base):
    name: str | None = None
    geofence_radius_km: float | None = None
    late_grace_minutes: int | None = None
    early_departure_grace_minutes: int | None = None


# ---------------------------------------------------------------- people
class CaregiverCreate(_Base):
    name: str
    phone: str | None = None
    email: str | None = None
    hire_date: datetime | None = None
    status: str = "active"
    skills: list[str] | None = None
    provider_id: str | None = None


class CaregiverRead(CaregiverCreate):
    id: int
    created_at: datetime | None = None
    avg_rating: float | None = None


class ClientCreate(_Base):
    name: str
    address: str | None = None
    phone: str | None = None
    payer: str = "private"
    status: str = "active"
    latitude: float | None = None
    longitude: float | None = None
    medicaid_id: str | None = None


class ClientRead(ClientCreate):
    id: int
    created_at: datetime | None = None


# ------------------------------------------------------------- scheduling
class ShiftCreate(_Base):
    client_id: int
    caregiver_id: int | None = None
    starts_at: datetime
    ends_at: datetime
    service_type: str = "personal_care"
    notes: str | None = None


class ShiftRead(ShiftCreate):
    id: int
    status: str
    created_at: datetime | None = None


class ShiftTradeCreate(_Base):
    shift_id: int
    requester_id: int
    offered_to_id: int | None = None
    note: str | None = None


class ShiftTradeRead(ShiftTradeCreate):
    id: int
    status: str
    decided_at: datetime | None = None
    created_at: datetime | None = None


class ShiftTradeDecision(_Base):
    approve: bool
    decided_by: int | None = None  # admin/coordinator caregiver id


# ------------------------------------------------------------------ EVV
class VisitCheckIn(_Base):
    shift_id: int
    latitude: float | None = None
    longitude: float | None = None
    method: str = "gps"
    # Manual correction (office "visit maintenance"): backdate the check-in.
    check_in_at: datetime | None = None


class VisitCheckOut(_Base):
    latitude: float | None = None
    longitude: float | None = None
    # Manual correction: set the check-out time explicitly.
    check_out_at: datetime | None = None


class VisitRead(_Base):
    id: int
    shift_id: int
    client_id: int
    caregiver_id: int
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    check_in_lat: float | None = None
    check_in_lng: float | None = None
    check_out_lat: float | None = None
    check_out_lng: float | None = None
    method: str
    verification_status: str
    flags: list[str] | None = None
    service_type: str
    service_code: str
    created_at: datetime | None = None


class VisitEventRead(_Base):
    id: int
    visit_id: int
    event_type: str
    at: datetime
    latitude: float | None = None
    longitude: float | None = None
    method: str


class VisitExceptionRead(_Base):
    id: int
    visit_id: int | None = None
    shift_id: int | None = None
    exception_type: str
    status: str
    detail: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    created_at: datetime | None = None


class VisitExceptionResolve(_Base):
    resolution_note: str | None = None


class EvvReport(_Base):
    period_start: datetime
    period_end: datetime
    total_visits: int
    verified: int
    flagged: int
    pending: int
    exceptions_by_type: dict[str, int]
    open_exceptions: int
    total_hours: float
    on_time_pct: float | None


class EvvBatchCreate(_Base):
    payer: str
    period_start: datetime
    period_end: datetime


class EvvBatchRead(_Base):
    id: int
    payer: str
    period_start: datetime
    period_end: datetime
    visit_count: int
    payload: list | None = None
    status: str
    created_at: datetime | None = None


# -------------------------------------------------------- authorizations
class AuthorizationCreate(_Base):
    client_id: int
    service_type: str
    payer: str
    units_authorized: float
    unit_label: str = "hours"
    start_date: datetime
    end_date: datetime


class AuthorizationRead(AuthorizationCreate):
    id: int
    units_used: float
    units_remaining: float
    status: str
    created_at: datetime | None = None


# --------------------------------------------------------------- billing
class ClaimCreate(_Base):
    client_id: int
    authorization_id: int | None = None
    payer: str
    service_date_from: datetime
    service_date_to: datetime
    visit_ids: list[int] | None = None
    rate_per_unit: float = 32.0


class ClaimRead(_Base):
    id: int
    client_id: int
    authorization_id: int | None = None
    payer: str
    service_date_from: datetime
    service_date_to: datetime
    visit_ids: list[int] | None = None
    units: float
    amount: float
    status: str
    submitted_at: datetime | None = None
    adjudicated_at: datetime | None = None
    denial_reason: str | None = None
    created_at: datetime | None = None


class ClaimAdjudicate(_Base):
    pay: bool
    denial_reason: str | None = None


# --------------------------------------------------------------- payroll
class TimesheetRead(_Base):
    id: int
    caregiver_id: int
    visit_id: int | None = None
    shift_id: int | None = None
    clock_in: datetime
    clock_out: datetime
    hours: float
    overtime_hours: float
    status: str
    pay_rate: float | None = None
    created_at: datetime | None = None


class TimesheetApprove(_Base):
    approve: bool
    pay_rate: float | None = None


# ------------------------------------------------------- vendors & reimb
class VendorCreate(_Base):
    name: str
    vendor_type: str = "supplier"
    contact: str | None = None


class VendorRead(VendorCreate):
    id: int
    created_at: datetime | None = None


class VendorInvoiceCreate(_Base):
    vendor_id: int
    amount: float
    description: str | None = None
    due_date: datetime | None = None


class VendorInvoiceRead(VendorInvoiceCreate):
    id: int
    status: str
    paid_at: datetime | None = None
    created_at: datetime | None = None


class ReimbursementCreate(_Base):
    caregiver_id: int
    amount: float
    category: str = "mileage"
    receipt_ref: str | None = None


class ReimbursementRead(ReimbursementCreate):
    id: int
    status: str
    decided_at: datetime | None = None
    created_at: datetime | None = None


class StatusDecision(_Base):
    """Generic approve/pay/decline style decision."""
    approve: bool


# -------------------------------------------------------------- training
class TrainingCourseCreate(_Base):
    title: str
    description: str | None = None
    required: bool = True
    validity_days: int | None = None


class TrainingCourseRead(TrainingCourseCreate):
    id: int
    created_at: datetime | None = None


class TrainingAssignmentCreate(_Base):
    caregiver_id: int
    course_id: int
    due_date: datetime | None = None


class TrainingAssignmentRead(_Base):
    id: int
    caregiver_id: int
    course_id: int
    assigned_date: datetime | None = None
    due_date: datetime | None = None
    completed_date: datetime | None = None
    status: str
    created_at: datetime | None = None


# -------------------------------------------------------- care management
class CarePlanCreate(_Base):
    client_id: int
    start_date: datetime | None = None
    goals: list[str] | None = None


class CarePlanRead(_Base):
    id: int
    client_id: int
    start_date: datetime | None = None
    goals: list[str] | None = None
    status: str
    created_at: datetime | None = None


class CareNoteCreate(_Base):
    care_plan_id: int
    caregiver_id: int | None = None
    note: str
    note_type: str = "progress"


class CareNoteRead(CareNoteCreate):
    id: int
    created_at: datetime | None = None


# --------------------------------------------------------------- ratings
class RatingCreate(_Base):
    caregiver_id: int
    client_id: int | None = None
    score: int = Field(ge=1, le=5)
    comment: str | None = None


class RatingRead(RatingCreate):
    id: int
    created_at: datetime | None = None


class CaregiverRatingSummary(_Base):
    caregiver_id: int
    count: int
    average: float | None


# --------------------------------------------------------------- workflows
class WorkflowRuleCreate(_Base):
    name: str
    trigger_event: str
    condition: dict | None = None
    action: dict | None = None
    active: bool = True


class WorkflowRuleRead(WorkflowRuleCreate):
    id: int
    created_at: datetime | None = None


class WorkflowLogRead(_Base):
    id: int
    rule_id: int | None = None
    event: str
    payload: dict | None = None
    result: str
    created_at: datetime | None = None


class WorkflowTestEvent(_Base):
    event: str
    payload: dict = {}
