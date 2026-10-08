"""Training & compliance tracking, care management, and custom workflows."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..workflows import fire_event

router = APIRouter()


# ------------------------------------------------------- training courses
@router.post("/training-courses", response_model=schemas.TrainingCourseRead, status_code=201)
def create_course(payload: schemas.TrainingCourseCreate, db: Session = Depends(get_db)):
    course = models.TrainingCourse(**payload.model_dump())
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


@router.get("/training-courses", response_model=list[schemas.TrainingCourseRead])
def list_courses(db: Session = Depends(get_db)):
    return db.query(models.TrainingCourse).order_by(models.TrainingCourse.title).all()


# ------------------------------------------------- training assignments
@router.post("/training-assignments", response_model=schemas.TrainingAssignmentRead, status_code=201)
def assign_training(payload: schemas.TrainingAssignmentCreate, db: Session = Depends(get_db)):
    if not db.get(models.Caregiver, payload.caregiver_id):
        raise HTTPException(404, "caregiver not found")
    if not db.get(models.TrainingCourse, payload.course_id):
        raise HTTPException(404, "course not found")
    assignment = models.TrainingAssignment(
        caregiver_id=payload.caregiver_id,
        course_id=payload.course_id,
        assigned_date=datetime.utcnow(),
        due_date=payload.due_date,
        status="assigned",
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.get("/training-assignments", response_model=list[schemas.TrainingAssignmentRead])
def list_assignments(
    caregiver_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)
):
    q = db.query(models.TrainingAssignment)
    if caregiver_id:
        q = q.filter(models.TrainingAssignment.caregiver_id == caregiver_id)
    if status:
        q = q.filter(models.TrainingAssignment.status == status)
    return q.order_by(models.TrainingAssignment.due_date).all()


def _refresh_overdue(db: Session) -> None:
    """Lazily mark past-due assignments as overdue (v0.1: computed on read)."""
    now = datetime.utcnow()
    stale = (
        db.query(models.TrainingAssignment)
        .filter(
            models.TrainingAssignment.status.in_(["assigned", "in_progress"]),
            models.TrainingAssignment.due_date.isnot(None),
            models.TrainingAssignment.due_date < now,
        )
        .all()
    )
    for a in stale:
        a.status = "overdue"
        fire_event(
            db,
            "training.overdue",
            {"assignment_id": a.id, "caregiver_id": a.caregiver_id, "course_id": a.course_id},
        )
    if stale:
        db.commit()


@router.get("/training-assignments/overdue/list", response_model=list[schemas.TrainingAssignmentRead])
def overdue_assignments(db: Session = Depends(get_db)):
    _refresh_overdue(db)
    return (
        db.query(models.TrainingAssignment)
        .filter(models.TrainingAssignment.status == "overdue")
        .order_by(models.TrainingAssignment.due_date)
        .all()
    )


@router.post("/training-assignments/{assignment_id}/complete",
             response_model=schemas.TrainingAssignmentRead)
def complete_training(assignment_id: int, db: Session = Depends(get_db)):
    assignment = db.get(models.TrainingAssignment, assignment_id)
    if not assignment:
        raise HTTPException(404, "assignment not found")
    assignment.status = "completed"
    assignment.completed_date = datetime.utcnow()
    db.commit()
    db.refresh(assignment)
    return assignment


# --------------------------------------------------------- care planning
@router.post("/care-plans", response_model=schemas.CarePlanRead, status_code=201)
def create_care_plan(payload: schemas.CarePlanCreate, db: Session = Depends(get_db)):
    if not db.get(models.Client, payload.client_id):
        raise HTTPException(404, "client not found")
    plan = models.CarePlan(
        client_id=payload.client_id,
        start_date=payload.start_date or datetime.utcnow(),
        goals=payload.goals,
        status="active",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/care-plans", response_model=list[schemas.CarePlanRead])
def list_care_plans(client_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(models.CarePlan)
    if client_id:
        q = q.filter(models.CarePlan.client_id == client_id)
    return q.order_by(models.CarePlan.created_at.desc()).all()


@router.get("/care-plans/{plan_id}", response_model=schemas.CarePlanRead)
def get_care_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(models.CarePlan, plan_id)
    if not plan:
        raise HTTPException(404, "care plan not found")
    return plan


@router.post("/care-notes", response_model=schemas.CareNoteRead, status_code=201)
def add_care_note(payload: schemas.CareNoteCreate, db: Session = Depends(get_db)):
    if not db.get(models.CarePlan, payload.care_plan_id):
        raise HTTPException(404, "care plan not found")
    note = models.CareNote(**payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/care-plans/{plan_id}/notes", response_model=list[schemas.CareNoteRead])
def list_care_notes(plan_id: int, db: Session = Depends(get_db)):
    if not db.get(models.CarePlan, plan_id):
        raise HTTPException(404, "care plan not found")
    return (
        db.query(models.CareNote)
        .filter(models.CareNote.care_plan_id == plan_id)
        .order_by(models.CareNote.created_at.desc())
        .all()
    )


# --------------------------------------------------------------- workflows
@router.post("/workflow-rules", response_model=schemas.WorkflowRuleRead, status_code=201)
def create_rule(payload: schemas.WorkflowRuleCreate, db: Session = Depends(get_db)):
    rule = models.WorkflowRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/workflow-rules", response_model=list[schemas.WorkflowRuleRead])
def list_rules(active: bool | None = None, db: Session = Depends(get_db)):
    q = db.query(models.WorkflowRule)
    if active is not None:
        q = q.filter(models.WorkflowRule.active.is_(active))
    return q.order_by(models.WorkflowRule.created_at.desc()).all()


@router.post("/workflow-rules/test", response_model=list[str])
def test_rule(payload: schemas.WorkflowTestEvent, db: Session = Depends(get_db)):
    """Fire a synthetic event to see which rules match (useful in the admin UI)."""
    return fire_event(db, payload.event, payload.payload)


@router.get("/workflow-logs", response_model=list[schemas.WorkflowLogRead])
def list_logs(event: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.WorkflowLog)
    if event:
        q = q.filter(models.WorkflowLog.event == event)
    return q.order_by(models.WorkflowLog.created_at.desc()).limit(200).all()
