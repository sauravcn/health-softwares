"""SQLAlchemy models for all 11 home-care modules.

Conventions: integer autoincrement primary keys, naive-UTC datetimes,
status fields as plain strings validated at the Pydantic layer.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, server_default=func.now())


# ------------------------------------------------------------ agency cfg
class Agency(Base, TimestampMixin):
    """Agency-level EVV business-rule configuration (v0.1: single agency)."""

    __tablename__ = "agencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    geofence_radius_km: Mapped[float] = mapped_column(Float, default=1.0)
    late_grace_minutes: Mapped[int] = mapped_column(Integer, default=15)
    early_departure_grace_minutes: Mapped[int] = mapped_column(Integer, default=15)


# ---------------------------------------------------------------- people
class Caregiver(Base, TimestampMixin):
    """A caregiver employed by the agency who visits clients."""

    __tablename__ = "caregivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(120))
    hire_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|inactive
    skills: Mapped[list | None] = mapped_column(JSON)  # e.g. ["CNA", "dementia"]
    provider_id: Mapped[str | None] = mapped_column(String(40))  # payer-issued provider ID

    shifts: Mapped[list["Shift"]] = relationship(back_populates="caregiver")
    ratings: Mapped[list["Rating"]] = relationship(back_populates="caregiver")


class Client(Base, TimestampMixin):
    """A care recipient served by the agency."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    payer: Mapped[str] = mapped_column(String(40), default="private")  # medicaid|medicare|private|va
    status: Mapped[str] = mapped_column(String(20), default="active")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    medicaid_id: Mapped[str | None] = mapped_column(String(40))  # member Medicaid ID for EVV feeds


# ------------------------------------------------------------- scheduling
class Shift(Base, TimestampMixin):
    """A scheduled visit of a caregiver at a client."""

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    service_type: Mapped[str] = mapped_column(String(60), default="personal_care")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    # scheduled|in_progress|completed|cancelled|missed|open
    notes: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship()
    caregiver: Mapped[Caregiver | None] = relationship(back_populates="shifts")


class ShiftTrade(Base, TimestampMixin):
    """A request from one caregiver to hand a shift to another."""

    __tablename__ = "shift_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    requester_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    offered_to_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|approved|declined|cancelled
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    shift: Mapped[Shift] = relationship()


# ------------------------------------------------------------------ EVV
class Visit(Base, TimestampMixin):
    """Electronic Visit Verification record for one shift's actual visit.

    EVV (federally required for Medicaid personal-care/home-health visits
    under the 21st Century Cures Act) proves the caregiver was at the
    client's home: who, what service, where (GPS), when (timestamps).
    """

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime)
    check_in_lat: Mapped[float | None] = mapped_column(Float)
    check_in_lng: Mapped[float | None] = mapped_column(Float)
    check_out_lat: Mapped[float | None] = mapped_column(Float)
    check_out_lng: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20), default="gps")  # gps|mobile|telephone|manual
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|verified|flagged
    flags: Mapped[list | None] = mapped_column(JSON)  # e.g. ["no_authorization","outside_geofence"]
    service_type: Mapped[str] = mapped_column(String(60), default="personal_care")
    service_code: Mapped[str] = mapped_column(String(20), default="T1019")  # billing/EVV service code

    shift: Mapped[Shift] = relationship()
    events: Mapped[list["VisitEvent"]] = relationship(back_populates="visit",
                                                     order_by="VisitEvent.at")
    exceptions: Mapped[list["VisitException"]] = relationship(back_populates="visit")


class VisitEvent(Base, TimestampMixin):
    """Immutable log of check-in / check-out events for a visit (audit trail)."""

    __tablename__ = "visit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # check_in|check_out
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20), default="gps")  # gps|mobile|telephone|manual

    visit: Mapped[Visit] = relationship(back_populates="events")


class VisitException(Base, TimestampMixin):
    """An EVV exception needing office review: open -> acknowledged -> resolved."""

    __tablename__ = "visit_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    exception_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # late_arrival|early_departure|missed_visit|gps_mismatch|no_show
    status: Mapped[str] = mapped_column(String(20), default="open")
    # open|acknowledged|resolved
    detail: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)

    visit: Mapped[Visit | None] = relationship(back_populates="exceptions")


class EvvBatch(Base, TimestampMixin):
    """Aggregator output: a batch of verified visits packaged for a payer."""

    __tablename__ = "evv_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payer: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visit_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[list | None] = mapped_column(JSON)  # serialized visit records
    status: Mapped[str] = mapped_column(String(20), default="ready")
    # ready|transmitted|acknowledged|rejected


# -------------------------------------------------------- authorizations
class Authorization(Base, TimestampMixin):
    """Payer authorization: how many units of a service a client may receive."""

    __tablename__ = "authorizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    service_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payer: Mapped[str] = mapped_column(String(40), nullable=False)
    units_authorized: Mapped[float] = mapped_column(Float, nullable=False)
    units_used: Mapped[float] = mapped_column(Float, default=0.0)
    unit_label: Mapped[str] = mapped_column(String(20), default="hours")
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active|exhausted|expired

    @property
    def units_remaining(self) -> float:
        return max(0.0, self.units_authorized - self.units_used)


# --------------------------------------------------------------- billing
class Claim(Base, TimestampMixin):
    """A billing claim sent to a payer for rendered visits."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    authorization_id: Mapped[int | None] = mapped_column(ForeignKey("authorizations.id"))
    payer: Mapped[str] = mapped_column(String(40), nullable=False)
    service_date_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    service_date_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    visit_ids: Mapped[list | None] = mapped_column(JSON)
    units: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft|submitted|paid|denied
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    adjudicated_at: Mapped[datetime | None] = mapped_column(DateTime)
    denial_reason: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------- payroll
class Timesheet(Base, TimestampMixin):
    """Payroll time & attendance entry derived from a completed visit."""

    __tablename__ = "timesheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    clock_in: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    clock_out: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hours: Mapped[float] = mapped_column(Float, default=0.0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|approved|paid
    pay_rate: Mapped[float | None] = mapped_column(Float)


# ------------------------------------------------------- vendors & reimb
class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor_type: Mapped[str] = mapped_column(String(60), default="supplier")
    contact: Mapped[str | None] = mapped_column(String(255))


class VendorInvoice(Base, TimestampMixin):
    __tablename__ = "vendor_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|approved|paid
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)


class Reimbursement(Base, TimestampMixin):
    """Employer reimbursement: mileage/supplies submitted by a caregiver."""

    __tablename__ = "reimbursements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(60), default="mileage")
    receipt_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    # submitted|approved|paid|declined
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


# -------------------------------------------------------------- training
class TrainingCourse(Base, TimestampMixin):
    __tablename__ = "training_courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(default=True)
    validity_days: Mapped[int | None] = mapped_column(Integer)  # renew after N days


class TrainingAssignment(Base, TimestampMixin):
    __tablename__ = "training_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    course_id: Mapped[int] = mapped_column(ForeignKey("training_courses.id"), nullable=False)
    assigned_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    completed_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="assigned")
    # assigned|in_progress|completed|overdue


# -------------------------------------------------------- care management
class CarePlan(Base, TimestampMixin):
    __tablename__ = "care_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    goals: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="active")


class CareNote(Base, TimestampMixin):
    __tablename__ = "care_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    care_plan_id: Mapped[int] = mapped_column(ForeignKey("care_plans.id"), nullable=False)
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"))
    note: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str] = mapped_column(String(40), default="progress")


# --------------------------------------------------------------- ratings
class Rating(Base, TimestampMixin):
    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"))
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5
    comment: Mapped[str | None] = mapped_column(Text)

    caregiver: Mapped[Caregiver] = relationship(back_populates="ratings")


# --------------------------------------------------------------- workflows
class WorkflowRule(Base, TimestampMixin):
    """A custom automation: when <trigger_event> fires, run <action> if
    the event payload satisfies <condition>."""

    __tablename__ = "workflow_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(80), nullable=False)
    # e.g. visit.checked_in, visit.checked_out, training.overdue, claim.denied
    condition: Mapped[dict | None] = mapped_column(JSON)
    # {"field": "flags", "contains": "no_authorization"} or {"field": "score", "lt": 3}
    action: Mapped[dict | None] = mapped_column(JSON)
    # {"type": "flag_visit", "flag": "review_needed"} | {"type": "create_alert", ...}
    active: Mapped[bool] = mapped_column(default=True)


class WorkflowLog(Base, TimestampMixin):
    __tablename__ = "workflow_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_rules.id"))
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(255), nullable=False)
