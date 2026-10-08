"""Scheduling & shift trading tests."""
from datetime import datetime, timedelta


def _shift(client, start, end, cg_id=None):
    body = {"client_id": client["id"], "starts_at": start.isoformat(),
            "ends_at": end.isoformat(), "service_type": "personal_care"}
    if cg_id is not None:
        body["caregiver_id"] = cg_id
    return body


def test_create_shift_and_conflict(client, sample):
    cg, cl = sample["caregiver"], sample["client"]
    now = datetime.utcnow()
    s1 = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=1),
                                                now + timedelta(hours=5), cg["id"]))
    assert s1.status_code == 201, s1.text
    assert s1.json()["status"] == "scheduled"

    # overlapping shift for the same caregiver -> 409
    s2 = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=3),
                                                now + timedelta(hours=6), cg["id"]))
    assert s2.status_code == 409

    # non-overlapping is fine
    s3 = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=6),
                                                now + timedelta(hours=8), cg["id"]))
    assert s3.status_code == 201


def test_open_shift_has_no_caregiver(client, sample):
    cl = sample["client"]
    now = datetime.utcnow()
    r = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=1), now + timedelta(hours=3)))
    assert r.status_code == 201
    assert r.json()["status"] == "open"
    assert r.json()["caregiver_id"] is None


def test_shift_trade_approve_reassigns(client, sample):
    cg, cl = sample["caregiver"], sample["client"]
    other = client.post("/api/caregivers", json={"name": "Second Caregiver"}).json()
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=1),
                                                   now + timedelta(hours=4), cg["id"])).json()

    trade = client.post("/api/trades", json={
        "shift_id": shift["id"], "requester_id": cg["id"],
        "offered_to_id": other["id"], "note": "family emergency"}).json()
    assert trade["status"] == "pending"

    decided = client.post(f"/api/trades/{trade['id']}/decide",
                          json={"approve": True}).json()
    assert decided["status"] == "approved"

    updated = client.get(f"/api/shifts/{shift['id']}").json()
    assert updated["caregiver_id"] == other["id"]


def test_trade_decline_keeps_assignment(client, sample):
    cg, cl = sample["caregiver"], sample["client"]
    other = client.post("/api/caregivers", json={"name": "Third Caregiver"}).json()
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=1),
                                                   now + timedelta(hours=4), cg["id"])).json()
    trade = client.post("/api/trades", json={
        "shift_id": shift["id"], "requester_id": cg["id"],
        "offered_to_id": other["id"]}).json()
    decided = client.post(f"/api/trades/{trade['id']}/decide",
                          json={"approve": False}).json()
    assert decided["status"] == "declined"
    updated = client.get(f"/api/shifts/{shift['id']}").json()
    assert updated["caregiver_id"] == cg["id"]


def test_trade_by_non_assigned_caregiver_rejected(client, sample):
    cg, cl = sample["caregiver"], sample["client"]
    stranger = client.post("/api/caregivers", json={"name": "Stranger"}).json()
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json=_shift(cl, now + timedelta(hours=1),
                                                   now + timedelta(hours=4), cg["id"])).json()
    r = client.post("/api/trades", json={"shift_id": shift["id"],
                                          "requester_id": stranger["id"]})
    assert r.status_code == 422
