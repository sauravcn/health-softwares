"""Caregivers, clients, and the caregiver rating system."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..workflows import fire_event

router = APIRouter()


def _avg_rating(db: Session, caregiver_id: int) -> float | None:
    avg = (
        db.query(func.avg(models.Rating.score))
        .filter(models.Rating.caregiver_id == caregiver_id)
        .scalar()
    )
    return round(float(avg), 2) if avg is not None else None


def _caregiver_read(db: Session, cg: models.Caregiver) -> schemas.CaregiverRead:
    data = schemas.CaregiverRead.model_validate(cg)
    data.avg_rating = _avg_rating(db, cg.id)
    return data


# ---------------------------------------------------------- caregivers
@router.post("/caregivers", response_model=schemas.CaregiverRead, status_code=201)
def create_caregiver(payload: schemas.CaregiverCreate, db: Session = Depends(get_db)):
    cg = models.Caregiver(**payload.model_dump())
    db.add(cg)
    db.commit()
    db.refresh(cg)
    return _caregiver_read(db, cg)


@router.get("/caregivers", response_model=list[schemas.CaregiverRead])
def list_caregivers(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Caregiver)
    if status:
        q = q.filter(models.Caregiver.status == status)
    return [_caregiver_read(db, cg) for cg in q.order_by(models.Caregiver.name).all()]


@router.get("/caregivers/{caregiver_id}", response_model=schemas.CaregiverRead)
def get_caregiver(caregiver_id: int, db: Session = Depends(get_db)):
    cg = db.get(models.Caregiver, caregiver_id)
    if not cg:
        raise HTTPException(404, "caregiver not found")
    return _caregiver_read(db, cg)


# ------------------------------------------------------------- clients
@router.post("/clients", response_model=schemas.ClientRead, status_code=201)
def create_client(payload: schemas.ClientCreate, db: Session = Depends(get_db)):
    client = models.Client(**payload.model_dump())
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.get("/clients", response_model=list[schemas.ClientRead])
def list_clients(payer: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Client)
    if payer:
        q = q.filter(models.Client.payer == payer)
    return q.order_by(models.Client.name).all()


@router.get("/clients/{client_id}", response_model=schemas.ClientRead)
def get_client(client_id: int, db: Session = Depends(get_db)):
    client = db.get(models.Client, client_id)
    if not client:
        raise HTTPException(404, "client not found")
    return client


# ------------------------------------------------------------- ratings
@router.post("/ratings", response_model=schemas.RatingRead, status_code=201)
def submit_rating(payload: schemas.RatingCreate, db: Session = Depends(get_db)):
    if not db.get(models.Caregiver, payload.caregiver_id):
        raise HTTPException(404, "caregiver not found")
    rating = models.Rating(**payload.model_dump())
    db.add(rating)
    db.commit()
    db.refresh(rating)
    fire_event(
        db,
        "rating.submitted",
        {"rating_id": rating.id, "caregiver_id": rating.caregiver_id, "score": rating.score},
    )
    return rating


@router.get("/ratings", response_model=list[schemas.RatingRead])
def list_ratings(caregiver_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Rating)
    if caregiver_id:
        q = q.filter(models.Rating.caregiver_id == caregiver_id)
    return q.order_by(models.Rating.created_at.desc()).all()


@router.get("/caregivers/{caregiver_id}/rating-summary", response_model=schemas.CaregiverRatingSummary)
def rating_summary(caregiver_id: int, db: Session = Depends(get_db)):
    if not db.get(models.Caregiver, caregiver_id):
        raise HTTPException(404, "caregiver not found")
    count = db.query(func.count(models.Rating.id)).filter(
        models.Rating.caregiver_id == caregiver_id
    ).scalar()
    return schemas.CaregiverRatingSummary(
        caregiver_id=caregiver_id, count=count, average=_avg_rating(db, caregiver_id)
    )
