"""Electronic Visit Verification (EVV) — the flagship module.

Enterprise-grade visit verification for home-care agencies:

* GPS/mobile/telephone/manual check-in & check-out with an immutable event log
* Per-agency business rules: late-arrival / early-departure grace periods and
  geofence radius (see ``Agency``)
* Auto-generated exception queue (late arrival, early departure, missed visit,
  GPS mismatch, no-show) with acknowledge -> resolve lifecycle
* Verification status lifecycle: pending -> verified | flagged
* Verification reports and state-aggregator-friendly export (CSV/JSON)
"""
import csv
import io
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..db import get_db
from ..workflows import fire_event

router = APIRouter()

METHODS = {"gps", "mobile", "telephone", "manual"}


# ------------------------------------------------------------ agency cfg
def get_agency(db: Session) -> models.Agency:
    agency = db.query(models.Agency).first()
    if not agency:
        agency = models.Agency(name=settings.agency_name)
        db.add(agency)
        db.commit()
        db.refresh(agency)
    return agency


@router.get("/agency", response_model=schemas.AgencyRead)
def read_agency(db: Session = Depends(get_db)):
    return get_agency(db)


@router.patch("/agency", response_model=schemas.AgencyRead)
def update_agency(payload: schemas.AgencyUpdate, db: Session = Depends(get_db)):
    agency = get_agency(db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(agency, field, value)
    db.commit()
    db.refresh(agency)
    return agency


# ---------------------------------------------------------------- helpers
def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _active_authorization(db: Session, client_id: int, service_type: str, at: datetime):
    return (
        db.query(models.Authorization)
        .filter(
            models.Authorization.client_id == client_id,
            models.Authorization.service_type == service_type,
            models.Authorization.status == "active",
            models.Authorization.start_date <= at,
            models.Authorization.end_date >= at,
            models.Authorization.units_used < models.Authorization.units_authorized,
        )
        .first()
    )


def _add_exception(db: Session, *, visit_id=None, shift_id=None,
                   exception_type: str, detail: str) -> models.VisitException:
    # idempotent: don't duplicate an open exception of the same type
    q = db.query(models.VisitException).filter(
        models.VisitException.exception_type == exception_type,
        models.VisitException.status == "open",
    )
    q = q.filter(models.VisitException.visit_id == visit_id) if visit_id \
        else q.filter(models.VisitException.shift_id == shift_id)
    existing = q.first()
    if existing:
        return existing
    exc = models.VisitException(
        visit_id=visit_id, shift_id=shift_id,
        exception_type=exception_type, detail=detail, status="open",
    )
    db.add(exc)
    return exc


# ------------------------------------------------------- core operations
def do_check_in(db: Session, shift: models.Shift, *, latitude=None,
                longitude=None, method="gps",
                check_in_at: datetime | None = None) -> models.Visit:
    """Shared check-in logic used by the API and the seed script."""
    if method not in METHODS:
        raise HTTPException(422, f"method must be one of {sorted(METHODS)}")
    if shift.status not in ("scheduled", "open"):
        raise HTTPException(422, f"shift is {shift.status}; cannot check in")
    if not shift.caregiver_id:
        raise HTTPException(422, "shift has no caregiver assigned")
    if db.query(models.Visit).filter(models.Visit.shift_id == shift.id).first():
        raise HTTPException(409, "shift already has a visit record")

    agency = get_agency(db)
    now = check_in_at or datetime.utcnow()
    flags: list[str] = []

    auth = _active_authorization(db, shift.client_id, shift.service_type, now)
    if not auth:
        flags.append("no_authorization")

    client = db.get(models.Client, shift.client_id)
    if (latitude is not None and client and client.latitude is not None
            and client.longitude is not None):
        dist = _haversine_km(latitude, longitude, client.latitude, client.longitude)
        if dist > agency.geofence_radius_km:
            flags.append("outside_geofence")

    visit = models.Visit(
        shift_id=shift.id, client_id=shift.client_id, caregiver_id=shift.caregiver_id,
        check_in_at=now, check_in_lat=latitude, check_in_lng=longitude,
        method=method, flags=flags or None, service_type=shift.service_type,
        verification_status="pending",
    )
    db.add(visit)
    db.flush()  # need visit.id for events/exceptions

    db.add(models.VisitEvent(visit_id=visit.id, event_type="check_in", at=now,
                             latitude=latitude, longitude=longitude, method=method))

    # Business rule: late arrival (beyond the agency grace period).
    late_by = (now - shift.starts_at).total_seconds() / 60
    grace = agency.late_grace_minutes
    if late_by > grace:
        flags.append("late_arrival")
        _add_exception(db, visit_id=visit.id, shift_id=shift.id,
                       exception_type="late_arrival",
                       detail=f"Checked in {late_by:.0f} min after scheduled start "
                              f"(grace: {grace} min).")
    # Business rule: GPS outside the agency geofence.
    if "outside_geofence" in flags:
        _add_exception(db, visit_id=visit.id, shift_id=shift.id,
                       exception_type="gps_mismatch",
                       detail=f"Check-in GPS outside {agency.geofence_radius_km} km "
                              f"geofence of client address.")

    visit.flags = flags or None
    shift.status = "in_progress"
    db.commit()
    db.refresh(visit)
    fire_event(db, "visit.checked_in",
               {"visit_id": visit.id, "shift_id": shift.id,
                "caregiver_id": visit.caregiver_id, "flags": flags})
    return visit


def do_check_out(db: Session, visit: models.Visit, *, latitude=None,
                 longitude=None,
                 check_out_at: datetime | None = None) -> models.Visit:
    """Shared check-out logic used by the API and the seed script."""
    if visit.check_out_at:
        raise HTTPException(409, "visit already checked out")
    now = check_out_at or datetime.utcnow()
    if now <= visit.check_in_at:
        raise HTTPException(422, "check_out_at must be after check_in_at")

    agency = get_agency(db)
    visit.check_out_at = now
    visit.check_out_lat = latitude
    visit.check_out_lng = longitude
    db.add(models.VisitEvent(visit_id=visit.id, event_type="check_out", at=now,
                             latitude=latitude, longitude=longitude,
                             method=visit.method))

    hours = max(0.0, (now - visit.check_in_at).total_seconds() / 3600)
    flags = list(visit.flags or [])

    auth = _active_authorization(db, visit.client_id, visit.service_type, now)
    if auth:
        auth.units_used = round(auth.units_used + hours, 2)
        if auth.units_used >= auth.units_authorized:
            auth.status = "exhausted"
    elif "no_authorization" not in flags:
        flags.append("no_authorization")

    # Business rule: early departure (beyond the agency grace period).
    shift = db.get(models.Shift, visit.shift_id)
    if shift:
        early_by = (shift.ends_at - now).total_seconds() / 60
        grace = agency.early_departure_grace_minutes
        if early_by > grace:
            flags.append("early_departure")
            _add_exception(db, visit_id=visit.id, shift_id=shift.id,
                           exception_type="early_departure",
                           detail=f"Checked out {early_by:.0f} min before scheduled end "
                                  f"(grace: {grace} min).")
        shift.status = "completed"

    visit.flags = flags or None
    visit.verification_status = "flagged" if flags else "verified"

    overtime = max(0.0, hours - 8.0)
    db.add(models.Timesheet(
        caregiver_id=visit.caregiver_id, visit_id=visit.id, shift_id=visit.shift_id,
        clock_in=visit.check_in_at, clock_out=now,
        hours=round(hours, 2), overtime_hours=round(overtime, 2), status="pending"))

    db.commit()
    db.refresh(visit)
    fire_event(db, "visit.checked_out",
               {"visit_id": visit.id, "caregiver_id": visit.caregiver_id,
                "hours": round(hours, 2),
                "verification_status": visit.verification_status, "flags": flags})
    return visit


def sweep_missed_visits(db: Session, at: datetime | None = None) -> int:
    """Flag shifts that ended with no check-in as missed-visit exceptions."""
    now = at or datetime.utcnow()
    shifts = (
        db.query(models.Shift)
        .filter(models.Shift.status == "scheduled",
                models.Shift.ends_at < now)
        .all()
    )
    count = 0
    for shift in shifts:
        has_visit = db.query(models.Visit).filter(
            models.Visit.shift_id == shift.id).first()
        if has_visit:
            continue
        _add_exception(db, shift_id=shift.id, exception_type="missed_visit",
                       detail=f"No check-in recorded for shift scheduled "
                              f"{shift.starts_at}–{shift.ends_at}.")
        shift.status = "missed"
        count += 1
    if count:
        db.commit()
    return count


# ------------------------------------------------------------------ API
@router.post("/visits/check-in", response_model=schemas.VisitRead, status_code=201)
def check_in(payload: schemas.VisitCheckIn, db: Session = Depends(get_db)):
    shift = db.get(models.Shift, payload.shift_id)
    if not shift:
        raise HTTPException(404, "shift not found")
    return do_check_in(db, shift, latitude=payload.latitude,
                       longitude=payload.longitude, method=payload.method,
                       check_in_at=payload.check_in_at)


@router.post("/visits/{visit_id}/check-out", response_model=schemas.VisitRead)
def check_out(visit_id: int, payload: schemas.VisitCheckOut, db: Session = Depends(get_db)):
    visit = db.get(models.Visit, visit_id)
    if not visit:
        raise HTTPException(404, "visit not found")
    return do_check_out(db, visit, latitude=payload.latitude,
                        longitude=payload.longitude,
                        check_out_at=payload.check_out_at)


@router.get("/visits", response_model=list[schemas.VisitRead])
def list_visits(verification_status: str | None = None,
                caregiver_id: int | None = None,
                client_id: int | None = None,
                db: Session = Depends(get_db)):
    q = db.query(models.Visit)
    if verification_status:
        q = q.filter(models.Visit.verification_status == verification_status)
    if caregiver_id:
        q = q.filter(models.Visit.caregiver_id == caregiver_id)
    if client_id:
        q = q.filter(models.Visit.client_id == client_id)
    return q.order_by(models.Visit.check_in_at.desc()).all()


@router.get("/visits/{visit_id}", response_model=schemas.VisitRead)
def get_visit(visit_id: int, db: Session = Depends(get_db)):
    visit = db.get(models.Visit, visit_id)
    if not visit:
        raise HTTPException(404, "visit not found")
    return visit


@router.get("/visits/{visit_id}/events", response_model=list[schemas.VisitEventRead])
def visit_events(visit_id: int, db: Session = Depends(get_db)):
    if not db.get(models.Visit, visit_id):
        raise HTTPException(404, "visit not found")
    return (db.query(models.VisitEvent)
            .filter(models.VisitEvent.visit_id == visit_id)
            .order_by(models.VisitEvent.at).all())


# ---------------------------------------------------------- exceptions
@router.get("/exceptions", response_model=list[schemas.VisitExceptionRead])
def exception_queue(status: str | None = None,
                    exception_type: str | None = None,
                    db: Session = Depends(get_db)):
    q = db.query(models.VisitException)
    if status:
        q = q.filter(models.VisitException.status == status)
    if exception_type:
        q = q.filter(models.VisitException.exception_type == exception_type)
    return q.order_by(models.VisitException.created_at.desc()).all()


@router.post("/exceptions/{exception_id}/acknowledge",
             response_model=schemas.VisitExceptionRead)
def acknowledge_exception(exception_id: int, db: Session = Depends(get_db)):
    exc = db.get(models.VisitException, exception_id)
    if not exc:
        raise HTTPException(404, "exception not found")
    if exc.status != "open":
        raise HTTPException(422, f"exception is {exc.status}")
    exc.status = "acknowledged"
    exc.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(exc)
    return exc


@router.post("/exceptions/{exception_id}/resolve",
             response_model=schemas.VisitExceptionRead)
def resolve_exception(exception_id: int, payload: schemas.VisitExceptionResolve,
                      db: Session = Depends(get_db)):
    exc = db.get(models.VisitException, exception_id)
    if not exc:
        raise HTTPException(404, "exception not found")
    if exc.status == "resolved":
        raise HTTPException(422, "exception already resolved")
    exc.status = "resolved"
    exc.resolved_at = datetime.utcnow()
    exc.resolution_note = payload.resolution_note
    db.commit()
    db.refresh(exc)
    return exc


@router.post("/sweep-missed")
def sweep_missed(db: Session = Depends(get_db)):
    """Run the missed-visit sweep (also runs automatically on report views)."""
    return {"missed_flagged": sweep_missed_visits(db)}


# ---------------------------------------------------------------- report
@router.get("/report", response_model=schemas.EvvReport)
def verification_report(period_start: datetime, period_end: datetime,
                        db: Session = Depends(get_db)):
    sweep_missed_visits(db)  # keep the exception queue fresh
    visits = (db.query(models.Visit)
              .filter(models.Visit.check_in_at >= period_start,
                      models.Visit.check_in_at < period_end).all())
    by_type = (db.query(models.VisitException.exception_type, func.count())
               .filter(models.VisitException.created_at >= period_start,
                       models.VisitException.created_at < period_end)
               .group_by(models.VisitException.exception_type).all())
    open_exc = (db.query(func.count(models.VisitException.id))
                .filter(models.VisitException.status == "open").scalar())
    hours = sum((v.check_out_at - v.check_in_at).total_seconds() / 3600
                for v in visits if v.check_in_at and v.check_out_at)
    total = len(visits)
    on_time = sum(1 for v in visits
                  if not (v.flags and any(f in ("late_arrival", "early_departure")
                                          for f in v.flags)))
    return schemas.EvvReport(
        period_start=period_start, period_end=period_end,
        total_visits=total,
        verified=sum(1 for v in visits if v.verification_status == "verified"),
        flagged=sum(1 for v in visits if v.verification_status == "flagged"),
        pending=sum(1 for v in visits if v.verification_status == "pending"),
        exceptions_by_type={t: c for t, c in by_type},
        open_exceptions=open_exc,
        total_hours=round(hours, 2),
        on_time_pct=round(100.0 * on_time / total, 1) if total else None,
    )


# ---------------------------------------------------------------- export
def _export_row(db: Session, visit: models.Visit) -> dict:
    """One row shaped like a state EVV aggregator feed record."""
    client = db.get(models.Client, visit.client_id)
    caregiver = db.get(models.Caregiver, visit.caregiver_id)
    return {
        "visit_id": visit.id,
        "member_medicaid_id": client.medicaid_id if client else None,
        "member_name": client.name if client else None,
        "provider_id": caregiver.provider_id if caregiver else None,
        "provider_name": caregiver.name if caregiver else None,
        "service_code": visit.service_code,
        "service_type": visit.service_type,
        "check_in": visit.check_in_at.isoformat() if visit.check_in_at else None,
        "check_out": visit.check_out_at.isoformat() if visit.check_out_at else None,
        "check_in_lat": visit.check_in_lat,
        "check_in_lng": visit.check_in_lng,
        "check_out_lat": visit.check_out_lat,
        "check_out_lng": visit.check_out_lng,
        "verification_method": visit.method,
        "verification_status": visit.verification_status,
        "flags": ",".join(visit.flags or []),
    }


@router.get("/export")
def export_visits(period_start: datetime, period_end: datetime,
                  format: str = "json", db: Session = Depends(get_db)):
    visits = (db.query(models.Visit)
              .filter(models.Visit.check_in_at >= period_start,
                      models.Visit.check_in_at < period_end)
              .order_by(models.Visit.check_in_at).all())
    rows = [_export_row(db, v) for v in visits]
    if format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else [
            "visit_id", "member_medicaid_id", "member_name", "provider_id",
            "provider_name", "service_code", "service_type", "check_in", "check_out",
            "check_in_lat", "check_in_lng", "check_out_lat", "check_out_lng",
            "verification_method", "verification_status", "flags"])
        writer.writeheader()
        writer.writerows(rows)
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 "attachment; filename=evv_export.csv"})
    if format != "json":
        raise HTTPException(422, "format must be 'json' or 'csv'")
    return rows


# ------------------------------------------------------- EVV aggregator
@router.post("/evv-batches", response_model=schemas.EvvBatchRead, status_code=201)
def create_batch(payload: schemas.EvvBatchCreate, db: Session = Depends(get_db)):
    if payload.period_end <= payload.period_start:
        raise HTTPException(422, "period_end must be after period_start")
    visits = (
        db.query(models.Visit)
        .join(models.Client, models.Visit.client_id == models.Client.id)
        .filter(
            models.Visit.verification_status == "verified",
            models.Visit.check_in_at >= payload.period_start,
            models.Visit.check_in_at < payload.period_end,
            models.Client.payer == payload.payer,
        )
        .order_by(models.Visit.check_in_at)
        .all()
    )
    batch = models.EvvBatch(
        payer=payload.payer, period_start=payload.period_start,
        period_end=payload.period_end, visit_count=len(visits),
        payload=[_export_row(db, v) for v in visits], status="ready",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/evv-batches", response_model=list[schemas.EvvBatchRead])
def list_batches(db: Session = Depends(get_db)):
    return db.query(models.EvvBatch).order_by(models.EvvBatch.created_at.desc()).all()


@router.post("/evv-batches/{batch_id}/transmit", response_model=schemas.EvvBatchRead)
def transmit_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(models.EvvBatch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    if batch.status != "ready":
        raise HTTPException(422, f"batch is {batch.status}; only 'ready' batches can transmit")
    # v0.1: mark transmitted. A real deployment would POST `payload` to the
    # state's EVV aggregator endpoint here and record the acknowledgement.
    batch.status = "transmitted"
    db.commit()
    db.refresh(batch)
    return batch


# ------------------------------------------------- legacy timesheet hook
# (do_check_out already creates the timesheet; nothing else needed here.)
