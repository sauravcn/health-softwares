"""Scheduling & shift trading."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db

router = APIRouter()

TERMINAL = {"cancelled", "completed", "missed"}


def _conflicts(
    db: Session, caregiver_id: int, starts_at: datetime, ends_at: datetime, ignore_id: int | None = None
) -> bool:
    q = db.query(models.Shift).filter(
        models.Shift.caregiver_id == caregiver_id,
        models.Shift.status.notin_(TERMINAL),
        models.Shift.starts_at < ends_at,
        models.Shift.ends_at > starts_at,
    )
    if ignore_id:
        q = q.filter(models.Shift.id != ignore_id)
    return q.first() is not None


@router.post("/shifts", response_model=schemas.ShiftRead, status_code=201)
def create_shift(payload: schemas.ShiftCreate, db: Session = Depends(get_db)):
    if not db.get(models.Client, payload.client_id):
        raise HTTPException(404, "client not found")
    if payload.caregiver_id and not db.get(models.Caregiver, payload.caregiver_id):
        raise HTTPException(404, "caregiver not found")
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(422, "ends_at must be after starts_at")
    status = "open" if payload.caregiver_id is None else "scheduled"
    if payload.caregiver_id and _conflicts(
        db, payload.caregiver_id, payload.starts_at, payload.ends_at
    ):
        raise HTTPException(409, "caregiver already has an overlapping shift")
    shift = models.Shift(
        client_id=payload.client_id,
        caregiver_id=payload.caregiver_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        service_type=payload.service_type,
        notes=payload.notes,
        status=status,
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/shifts", response_model=list[schemas.ShiftRead])
def list_shifts(
    caregiver_id: int | None = None,
    client_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.Shift)
    if caregiver_id:
        q = q.filter(models.Shift.caregiver_id == caregiver_id)
    if client_id:
        q = q.filter(models.Shift.client_id == client_id)
    if status:
        q = q.filter(models.Shift.status == status)
    return q.order_by(models.Shift.starts_at).all()


@router.get("/shifts/{shift_id}", response_model=schemas.ShiftRead)
def get_shift(shift_id: int, db: Session = Depends(get_db)):
    shift = db.get(models.Shift, shift_id)
    if not shift:
        raise HTTPException(404, "shift not found")
    return shift


@router.post("/shifts/{shift_id}/cancel", response_model=schemas.ShiftRead)
def cancel_shift(shift_id: int, db: Session = Depends(get_db)):
    shift = db.get(models.Shift, shift_id)
    if not shift:
        raise HTTPException(404, "shift not found")
    shift.status = "cancelled"
    db.commit()
    db.refresh(shift)
    return shift


# ------------------------------------------------------- shift trading
@router.post("/trades", response_model=schemas.ShiftTradeRead, status_code=201)
def request_trade(payload: schemas.ShiftTradeCreate, db: Session = Depends(get_db)):
    shift = db.get(models.Shift, payload.shift_id)
    if not shift:
        raise HTTPException(404, "shift not found")
    if shift.caregiver_id != payload.requester_id:
        raise HTTPException(422, "requester is not assigned to this shift")
    if shift.status in TERMINAL:
        raise HTTPException(422, "shift is already closed")
    if payload.offered_to_id and not db.get(models.Caregiver, payload.offered_to_id):
        raise HTTPException(404, "offered-to caregiver not found")
    trade = models.ShiftTrade(
        shift_id=payload.shift_id,
        requester_id=payload.requester_id,
        offered_to_id=payload.offered_to_id,
        note=payload.note,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


@router.get("/trades", response_model=list[schemas.ShiftTradeRead])
def list_trades(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.ShiftTrade)
    if status:
        q = q.filter(models.ShiftTrade.status == status)
    return q.order_by(models.ShiftTrade.created_at.desc()).all()


@router.post("/trades/{trade_id}/decide", response_model=schemas.ShiftTradeRead)
def decide_trade(trade_id: int, payload: schemas.ShiftTradeDecision, db: Session = Depends(get_db)):
    trade = db.get(models.ShiftTrade, trade_id)
    if not trade:
        raise HTTPException(404, "trade request not found")
    if trade.status != "pending":
        raise HTTPException(422, "trade request already decided")
    if payload.approve:
        if not trade.offered_to_id:
            raise HTTPException(422, "cannot approve a trade with no target caregiver")
        shift = db.get(models.Shift, trade.shift_id)
        if _conflicts(db, trade.offered_to_id, shift.starts_at, shift.ends_at, ignore_id=shift.id):
            raise HTTPException(409, "target caregiver has an overlapping shift")
        shift.caregiver_id = trade.offered_to_id
        trade.status = "approved"
    else:
        trade.status = "declined"
    trade.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(trade)
    return trade
