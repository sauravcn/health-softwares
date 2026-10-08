"""Real-time authorization management, billing/claims, payroll time &
attendance, vendor payments, and employer reimbursements."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..workflows import fire_event

router = APIRouter()


# ------------------------------------------------------- authorizations
@router.post("/authorizations", response_model=schemas.AuthorizationRead, status_code=201)
def create_authorization(payload: schemas.AuthorizationCreate, db: Session = Depends(get_db)):
    if not db.get(models.Client, payload.client_id):
        raise HTTPException(404, "client not found")
    if payload.end_date <= payload.start_date:
        raise HTTPException(422, "end_date must be after start_date")
    auth = models.Authorization(
        **payload.model_dump(), units_used=0.0, status="active"
    )
    db.add(auth)
    db.commit()
    db.refresh(auth)
    return auth


@router.get("/authorizations", response_model=list[schemas.AuthorizationRead])
def list_authorizations(
    client_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)
):
    q = db.query(models.Authorization)
    if client_id:
        q = q.filter(models.Authorization.client_id == client_id)
    if status:
        q = q.filter(models.Authorization.status == status)
    return q.order_by(models.Authorization.end_date).all()


@router.get("/authorizations/{auth_id}", response_model=schemas.AuthorizationRead)
def get_authorization(auth_id: int, db: Session = Depends(get_db)):
    auth = db.get(models.Authorization, auth_id)
    if not auth:
        raise HTTPException(404, "authorization not found")
    return auth


# --------------------------------------------------------------- billing
def _claim_units(db: Session, visit_ids: list[int] | None, client_id: int,
                 start: datetime, end: datetime) -> tuple[float, list[int]]:
    """Billable hours from verified, unbilled visits. Claims ownership of them
    implicitly by recording their ids on the claim (v0.1)."""
    q = db.query(models.Visit).filter(
        models.Visit.client_id == client_id,
        models.Visit.verification_status == "verified",
        models.Visit.check_in_at >= start,
        models.Visit.check_in_at < end,
    )
    if visit_ids:
        q = q.filter(models.Visit.id.in_(visit_ids))
    visits = q.all()
    hours = 0.0
    for v in visits:
        if v.check_in_at and v.check_out_at:
            hours += (v.check_out_at - v.check_in_at).total_seconds() / 3600
    return round(hours, 2), [v.id for v in visits]


@router.post("/claims", response_model=schemas.ClaimRead, status_code=201)
def create_claim(payload: schemas.ClaimCreate, db: Session = Depends(get_db)):
    if not db.get(models.Client, payload.client_id):
        raise HTTPException(404, "client not found")
    if payload.authorization_id and not db.get(models.Authorization, payload.authorization_id):
        raise HTTPException(404, "authorization not found")
    units, visit_ids = _claim_units(
        db, payload.visit_ids, payload.client_id,
        payload.service_date_from, payload.service_date_to,
    )
    claim = models.Claim(
        client_id=payload.client_id,
        authorization_id=payload.authorization_id,
        payer=payload.payer,
        service_date_from=payload.service_date_from,
        service_date_to=payload.service_date_to,
        visit_ids=visit_ids,
        units=units,
        amount=round(units * payload.rate_per_unit, 2),
        status="draft",
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


@router.get("/claims", response_model=list[schemas.ClaimRead])
def list_claims(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Claim)
    if status:
        q = q.filter(models.Claim.status == status)
    return q.order_by(models.Claim.created_at.desc()).all()


@router.get("/claims/{claim_id}", response_model=schemas.ClaimRead)
def get_claim(claim_id: int, db: Session = Depends(get_db)):
    claim = db.get(models.Claim, claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    return claim


@router.post("/claims/{claim_id}/submit", response_model=schemas.ClaimRead)
def submit_claim(claim_id: int, db: Session = Depends(get_db)):
    claim = db.get(models.Claim, claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    if claim.status != "draft":
        raise HTTPException(422, f"claim is {claim.status}; only drafts can be submitted")
    if claim.units <= 0:
        raise HTTPException(422, "claim has no billable units")
    claim.status = "submitted"
    claim.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(claim)
    return claim


@router.post("/claims/{claim_id}/adjudicate", response_model=schemas.ClaimRead)
def adjudicate_claim(claim_id: int, payload: schemas.ClaimAdjudicate, db: Session = Depends(get_db)):
    claim = db.get(models.Claim, claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    if claim.status != "submitted":
        raise HTTPException(422, f"claim is {claim.status}; only submitted claims can be adjudicated")
    claim.status = "paid" if payload.pay else "denied"
    claim.adjudicated_at = datetime.utcnow()
    if not payload.pay:
        claim.denial_reason = payload.denial_reason or "no reason given"
    db.commit()
    db.refresh(claim)
    fire_event(
        db,
        "claim.adjudicated",
        {"claim_id": claim.id, "status": claim.status, "amount": claim.amount},
    )
    return claim


# --------------------------------------------------------------- payroll
@router.get("/timesheets", response_model=list[schemas.TimesheetRead])
def list_timesheets(
    caregiver_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)
):
    q = db.query(models.Timesheet)
    if caregiver_id:
        q = q.filter(models.Timesheet.caregiver_id == caregiver_id)
    if status:
        q = q.filter(models.Timesheet.status == status)
    return q.order_by(models.Timesheet.clock_in.desc()).all()


@router.post("/timesheets/{timesheet_id}/approve", response_model=schemas.TimesheetRead)
def approve_timesheet(
    timesheet_id: int, payload: schemas.TimesheetApprove, db: Session = Depends(get_db)
):
    ts = db.get(models.Timesheet, timesheet_id)
    if not ts:
        raise HTTPException(404, "timesheet not found")
    if ts.status != "pending":
        raise HTTPException(422, f"timesheet is {ts.status}")
    if payload.approve:
        ts.status = "approved"
        if payload.pay_rate is not None:
            ts.pay_rate = payload.pay_rate
    else:
        ts.status = "pending"  # sent back for correction
    db.commit()
    db.refresh(ts)
    return ts


@router.get("/payroll/summary")
def payroll_summary(
    caregiver_id: int,
    period_start: datetime,
    period_end: datetime,
    db: Session = Depends(get_db),
):
    if not db.get(models.Caregiver, caregiver_id):
        raise HTTPException(404, "caregiver not found")
    rows = (
        db.query(
            func.coalesce(func.sum(models.Timesheet.hours), 0.0),
            func.coalesce(func.sum(models.Timesheet.overtime_hours), 0.0),
            func.count(models.Timesheet.id),
        )
        .filter(
            models.Timesheet.caregiver_id == caregiver_id,
            models.Timesheet.clock_in >= period_start,
            models.Timesheet.clock_in < period_end,
            models.Timesheet.status.in_(["approved", "paid"]),
        )
        .one()
    )
    return {
        "caregiver_id": caregiver_id,
        "period_start": period_start,
        "period_end": period_end,
        "total_hours": round(float(rows[0]), 2),
        "overtime_hours": round(float(rows[1]), 2),
        "entries": rows[2],
    }


# --------------------------------------------------------------- vendors
@router.post("/vendors", response_model=schemas.VendorRead, status_code=201)
def create_vendor(payload: schemas.VendorCreate, db: Session = Depends(get_db)):
    vendor = models.Vendor(**payload.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


@router.get("/vendors", response_model=list[schemas.VendorRead])
def list_vendors(db: Session = Depends(get_db)):
    return db.query(models.Vendor).order_by(models.Vendor.name).all()


@router.post("/vendor-invoices", response_model=schemas.VendorInvoiceRead, status_code=201)
def create_invoice(payload: schemas.VendorInvoiceCreate, db: Session = Depends(get_db)):
    if not db.get(models.Vendor, payload.vendor_id):
        raise HTTPException(404, "vendor not found")
    invoice = models.VendorInvoice(**payload.model_dump(), status="pending")
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/vendor-invoices", response_model=list[schemas.VendorInvoiceRead])
def list_invoices(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.VendorInvoice)
    if status:
        q = q.filter(models.VendorInvoice.status == status)
    return q.order_by(models.VendorInvoice.created_at.desc()).all()


@router.post("/vendor-invoices/{invoice_id}/pay", response_model=schemas.VendorInvoiceRead)
def pay_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.get(models.VendorInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "invoice not found")
    if invoice.status == "paid":
        raise HTTPException(422, "invoice already paid")
    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    db.commit()
    db.refresh(invoice)
    return invoice


# -------------------------------------------------------- reimbursements
@router.post("/reimbursements", response_model=schemas.ReimbursementRead, status_code=201)
def create_reimbursement(payload: schemas.ReimbursementCreate, db: Session = Depends(get_db)):
    if not db.get(models.Caregiver, payload.caregiver_id):
        raise HTTPException(404, "caregiver not found")
    reimb = models.Reimbursement(**payload.model_dump(), status="submitted")
    db.add(reimb)
    db.commit()
    db.refresh(reimb)
    return reimb


@router.get("/reimbursements", response_model=list[schemas.ReimbursementRead])
def list_reimbursements(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Reimbursement)
    if status:
        q = q.filter(models.Reimbursement.status == status)
    return q.order_by(models.Reimbursement.created_at.desc()).all()


@router.post("/reimbursements/{reimb_id}/decide", response_model=schemas.ReimbursementRead)
def decide_reimbursement(
    reimb_id: int, payload: schemas.StatusDecision, db: Session = Depends(get_db)
):
    reimb = db.get(models.Reimbursement, reimb_id)
    if not reimb:
        raise HTTPException(404, "reimbursement not found")
    if reimb.status not in ("submitted",):
        raise HTTPException(422, f"reimbursement is {reimb.status}")
    reimb.status = "approved" if payload.approve else "declined"
    reimb.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(reimb)
    return reimb
