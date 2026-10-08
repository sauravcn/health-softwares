"""Tiny event-driven workflow engine.

Routers call :func:`fire_event` at domain moments (visit check-in/out,
rating submitted, training overdue, claim denied, ...). Active
:class:`~app.models.WorkflowRule` rows whose ``trigger_event`` matches are
evaluated against the payload and their actions executed.

Condition format (all optional keys in one dict):
    {"field": "score", "lt": 3}
    {"field": "flags", "contains": "no_authorization"}
    {"field": "status", "eq": "denied"}

Action format:
    {"type": "flag_visit", "flag": "review_needed"}   # payload must carry visit_id
    {"type": "alert", "message": "..."}               # recorded in WorkflowLog
    {"type": "log", "message": "..."}
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import models


def _matches(condition: dict | None, payload: dict) -> bool:
    if not condition:
        return True
    field = condition.get("field")
    value = payload.get(field) if field else None
    if "eq" in condition:
        return value == condition["eq"]
    if "lt" in condition:
        try:
            return float(value) < float(condition["lt"])
        except (TypeError, ValueError):
            return False
    if "gt" in condition:
        try:
            return float(value) > float(condition["gt"])
        except (TypeError, ValueError):
            return False
    if "contains" in condition:
        return condition["contains"] in (value or [])
    return True


def _run_action(db: Session, action: dict, payload: dict) -> str:
    kind = (action or {}).get("type")
    if kind == "flag_visit" and payload.get("visit_id"):
        visit = db.get(models.Visit, payload["visit_id"])
        if visit:
            flags = list(visit.flags or [])
            flag = action.get("flag", "review_needed")
            if flag not in flags:
                flags.append(flag)
            visit.flags = flags
            return f"flagged visit {visit.id} with '{flag}'"
        return "flag_visit: visit not found"
    if kind in ("alert", "log"):
        return f"{kind.upper()}: {action.get('message', '(no message)')}"
    return f"unknown action type '{kind}' (no-op)"


def fire_event(db: Session, event: str, payload: dict | None = None) -> list[str]:
    """Evaluate all active rules for *event*; returns the action results."""
    payload = payload or {}
    results: list[str] = []
    rules = (
        db.query(models.WorkflowRule)
        .filter(models.WorkflowRule.trigger_event == event, models.WorkflowRule.active.is_(True))
        .all()
    )
    for rule in rules:
        if not _matches(rule.condition, payload):
            continue
        result = _run_action(db, rule.action or {}, payload)
        log_result = f"rule '{rule.name}': {result}"
        results.append(log_result)
        db.add(models.WorkflowLog(rule_id=rule.id, event=event, payload=payload, result=log_result))
    if results:
        db.commit()
    return results
