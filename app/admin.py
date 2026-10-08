"""Server-rendered admin UI (Jinja2), in the style of a back-office dashboard."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .db import get_db
from .routers.evv import get_agency, sweep_missed_visits

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _ctx(request: Request, **kw):
    return {"request": request, "agency": settings.agency_name, "now": datetime.utcnow(), **kw}


@router.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "caregivers": db.query(func.count(models.Caregiver.id)).scalar(),
        "clients": db.query(func.count(models.Client.id)).scalar(),
        "shifts_today": db.query(func.count(models.Shift.id))
        .filter(models.Shift.starts_at >= today, models.Shift.starts_at < today + timedelta(days=1))
        .scalar(),
        "open_shifts": db.query(func.count(models.Shift.id))
        .filter(models.Shift.status == "open").scalar(),
        "flagged_visits": db.query(func.count(models.Visit.id))
        .filter(models.Visit.verification_status == "flagged").scalar(),
        "pending_trades": db.query(func.count(models.ShiftTrade.id))
        .filter(models.ShiftTrade.status == "pending").scalar(),
        "claims_draft": db.query(func.count(models.Claim.id))
        .filter(models.Claim.status == "draft").scalar(),
        "overdue_training": db.query(func.count(models.TrainingAssignment.id))
        .filter(models.TrainingAssignment.status == "overdue").scalar(),
    }
    recent_visits = (
        db.query(models.Visit).order_by(models.Visit.check_in_at.desc()).limit(8).all()
    )
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, stats=stats, visits=recent_visits))


@router.get("/admin/shifts", response_class=HTMLResponse)
def shifts_page(request: Request, db: Session = Depends(get_db)):
    shifts = db.query(models.Shift).order_by(models.Shift.starts_at.desc()).limit(100).all()
    caregivers = {c.id: c.name for c in db.query(models.Caregiver).all()}
    clients = {c.id: c.name for c in db.query(models.Client).all()}
    trades = (
        db.query(models.ShiftTrade)
        .filter(models.ShiftTrade.status == "pending")
        .order_by(models.ShiftTrade.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "shifts.html", _ctx(request, shifts=shifts, caregivers=caregivers, clients=clients, trades=trades)
    )


@router.get("/admin/evv", response_class=HTMLResponse)
def evv_dashboard(request: Request, db: Session = Depends(get_db)):
    sweep_missed_visits(db)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    visits_today = (
        db.query(models.Visit)
        .filter(models.Visit.check_in_at >= today, models.Visit.check_in_at < tomorrow)
        .order_by(models.Visit.check_in_at).all()
    )
    upcoming = (
        db.query(models.Shift)
        .filter(models.Shift.status.in_(["scheduled", "open"]),
                models.Shift.starts_at >= today, models.Shift.starts_at < tomorrow)
        .order_by(models.Shift.starts_at).all()
    )
    exceptions = (
        db.query(models.VisitException)
        .filter(models.VisitException.status.in_(["open", "acknowledged"]))
        .order_by(models.VisitException.created_at.desc()).all()
    )
    week_ago = today - timedelta(days=7)
    visits_week = (db.query(models.Visit)
                   .filter(models.Visit.check_in_at >= week_ago).all())
    report = {
        "total": len(visits_week),
        "verified": sum(1 for v in visits_week if v.verification_status == "verified"),
        "flagged": sum(1 for v in visits_week if v.verification_status == "flagged"),
        "open_exceptions": db.query(func.count(models.VisitException.id))
                             .filter(models.VisitException.status == "open").scalar(),
    }
    caregivers = {c.id: c.name for c in db.query(models.Caregiver).all()}
    clients = {c.id: c.name for c in db.query(models.Client).all()}
    agency = get_agency(db)
    return templates.TemplateResponse(
        request, "evv_dashboard.html",
        _ctx(request, visits_today=visits_today, upcoming=upcoming,
             exceptions=exceptions, report=report, caregivers=caregivers,
             clients=clients, agency=agency, today=today,
             week_start=today - timedelta(days=7), tomorrow=tomorrow))


@router.post("/admin/exceptions/{exception_id}/acknowledge", include_in_schema=False)
def admin_ack_exception(exception_id: int, db: Session = Depends(get_db)):
    exc = db.get(models.VisitException, exception_id)
    if exc and exc.status == "open":
        exc.status = "acknowledged"
        exc.acknowledged_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/admin/evv", status_code=303)


@router.post("/admin/exceptions/{exception_id}/resolve", include_in_schema=False)
def admin_resolve_exception(request: Request, exception_id: int,
                            db: Session = Depends(get_db)):
    exc = db.get(models.VisitException, exception_id)
    if exc and exc.status != "resolved":
        exc.status = "resolved"
        exc.resolved_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/admin/evv", status_code=303)


@router.get("/admin/visits", response_class=HTMLResponse)
def visits_page(request: Request, db: Session = Depends(get_db)):
    visits = db.query(models.Visit).order_by(models.Visit.check_in_at.desc()).limit(100).all()
    caregivers = {c.id: c.name for c in db.query(models.Caregiver).all()}
    clients = {c.id: c.name for c in db.query(models.Client).all()}
    return templates.TemplateResponse(
        request, "visits.html", _ctx(request, visits=visits, caregivers=caregivers, clients=clients)
    )


@router.get("/admin/claims", response_class=HTMLResponse)
def claims_page(request: Request, db: Session = Depends(get_db)):
    claims = db.query(models.Claim).order_by(models.Claim.created_at.desc()).limit(100).all()
    clients = {c.id: c.name for c in db.query(models.Client).all()}
    batches = db.query(models.EvvBatch).order_by(models.EvvBatch.created_at.desc()).limit(20).all()
    return templates.TemplateResponse(
        request, "claims.html", _ctx(request, claims=claims, clients=clients, batches=batches)
    )


@router.get("/admin/caregivers", response_class=HTMLResponse)
def caregivers_page(request: Request, db: Session = Depends(get_db)):
    caregivers = db.query(models.Caregiver).order_by(models.Caregiver.name).all()
    summaries = {}
    for cg in caregivers:
        count = db.query(func.count(models.Rating.id)).filter(
            models.Rating.caregiver_id == cg.id).scalar()
        avg = db.query(func.avg(models.Rating.score)).filter(
            models.Rating.caregiver_id == cg.id).scalar()
        summaries[cg.id] = {"count": count, "avg": round(float(avg), 2) if avg else None}
    overdue = (
        db.query(models.TrainingAssignment)
        .filter(models.TrainingAssignment.status == "overdue").all()
    )
    cg_names = {c.id: c.name for c in caregivers}
    courses = {c.id: c.title for c in db.query(models.TrainingCourse).all()}
    return templates.TemplateResponse(
        request, "caregivers.html",
        _ctx(request, caregivers=caregivers, summaries=summaries,
             overdue=overdue, cg_names=cg_names, courses=courses),
    )


@router.get("/admin/workflows", response_class=HTMLResponse)
def workflows_page(request: Request, db: Session = Depends(get_db)):
    rules = db.query(models.WorkflowRule).order_by(models.WorkflowRule.created_at.desc()).all()
    logs = db.query(models.WorkflowLog).order_by(models.WorkflowLog.created_at.desc()).limit(50).all()
    return templates.TemplateResponse(request, "workflows.html", _ctx(request, rules=rules, logs=logs))
