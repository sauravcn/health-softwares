"""People/ratings, training & compliance, care management, workflows."""
from datetime import datetime, timedelta


def test_caregiver_client_crud(client):
    cg = client.post("/api/caregivers", json={"name": "CRUD CG", "skills": ["CNA"]}).json()
    assert cg["id"]
    assert client.get(f"/api/caregivers/{cg['id']}").json()["name"] == "CRUD CG"
    assert client.get("/api/caregivers/99999").status_code == 404

    cl = client.post("/api/clients", json={"name": "CRUD Client", "payer": "private"}).json()
    assert client.get(f"/api/clients/{cl['id']}").json()["payer"] == "private"


def test_rating_average_and_low_rating_workflow(client, sample):
    cg_id = sample["caregiver"]["id"]
    # rule: alert when a rating below 3 is submitted
    client.post("/api/workflow-rules", json={
        "name": "low rating alert", "trigger_event": "rating.submitted",
        "condition": {"field": "score", "lt": 3},
        "action": {"type": "alert", "message": "follow up"}})

    client.post("/api/ratings", json={"caregiver_id": cg_id, "score": 5})
    client.post("/api/ratings", json={"caregiver_id": cg_id, "score": 3})
    summary = client.get(f"/api/caregivers/{cg_id}/rating-summary").json()
    assert summary["count"] == 2
    assert summary["average"] == 4.0

    client.post("/api/ratings", json={"caregiver_id": cg_id, "score": 1,
                                      "comment": "no-show"})
    logs = client.get("/api/workflow-logs", params={"event": "rating.submitted"}).json()
    assert any("low rating alert" in l["result"] for l in logs)


def test_rating_score_bounds(client, sample):
    r = client.post("/api/ratings", json={"caregiver_id": sample["caregiver"]["id"], "score": 9})
    assert r.status_code == 422


def test_training_assign_complete_overdue(client, sample):
    cg_id = sample["caregiver"]["id"]
    course = client.post("/api/training-courses",
                         json={"title": "Test Course", "required": True}).json()
    past = (datetime.utcnow() - timedelta(days=2)).isoformat()
    a = client.post("/api/training-assignments", json={
        "caregiver_id": cg_id, "course_id": course["id"], "due_date": past}).json()
    assert a["status"] == "assigned"

    # reading the overdue list lazily marks it overdue and fires training.overdue
    overdue = client.get("/api/training-assignments/overdue/list").json()
    assert any(x["id"] == a["id"] for x in overdue)

    done = client.post(f"/api/training-assignments/{a['id']}/complete").json()
    assert done["status"] == "completed"
    assert done["completed_date"]
    assert client.get("/api/training-assignments/overdue/list").json() == []


def test_care_plan_and_notes(client, sample):
    plan = client.post("/api/care-plans", json={
        "client_id": sample["client"]["id"],
        "goals": ["ambulate with walker", "med reminders"]}).json()
    assert plan["status"] == "active"

    note = client.post("/api/care-notes", json={
        "care_plan_id": plan["id"],
        "caregiver_id": sample["caregiver"]["id"],
        "note": "Client did great today."}).json()
    assert note["id"]

    notes = client.get(f"/api/care-plans/{plan['id']}/notes").json()
    assert len(notes) == 1
    assert notes[0]["note"] == "Client did great today."


def test_workflow_rule_test_endpoint(client):
    rule = client.post("/api/workflow-rules", json={
        "name": "test rule", "trigger_event": "custom.ping",
        "condition": {"field": "level", "gt": 5},
        "action": {"type": "log", "message": "pong"}}).json()
    out = client.post("/api/workflow-rules/test",
                      json={"event": "custom.ping", "payload": {"level": 9}}).json()
    assert any("test rule" in line for line in out)
    # non-matching payload fires nothing
    out2 = client.post("/api/workflow-rules/test",
                       json={"event": "custom.ping", "payload": {"level": 1}}).json()
    assert out2 == []
    assert rule["active"] is True


def test_flag_visit_workflow_action(client, sample):
    # rule that flags a visit when check-in carries no_authorization
    client.post("/api/workflow-rules", json={
        "name": "flag unauth visits", "trigger_event": "visit.checked_in",
        "condition": {"field": "flags", "contains": "no_authorization"},
        "action": {"type": "flag_visit", "flag": "billing_review"}})

    cg = client.post("/api/caregivers", json={"name": "WF CG"}).json()
    cl = client.post("/api/clients", json={"name": "WF Client", "payer": "private"}).json()
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now - timedelta(minutes=5)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat()}).json()
    visit = client.post("/api/visits/check-in", json={"shift_id": shift["id"]}).json()
    fetched = client.get(f"/api/visits/{visit['id']}").json()
    assert "billing_review" in (fetched["flags"] or [])
