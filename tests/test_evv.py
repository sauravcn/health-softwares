"""EVV (flagship module) tests: check-in/check-out, verification flags,
authorization consumption, timesheet generation, and the aggregator."""
from datetime import datetime, timedelta


def _make_shift(client, sample, **kw):
    cg, cl = sample["caregiver"], sample["client"]
    now = datetime.utcnow()
    body = {"client_id": cl["id"], "caregiver_id": cg["id"],
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(hours=4)).isoformat(),
            "service_type": "personal_care"}
    body.update(kw)
    r = client.post("/api/shifts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_checkin_checkout_verified_with_authorization(client, sample):
    cl = sample["client"]
    shift = _make_shift(client, sample)
    two_h_ago = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    # GPS at the client's registered address
    ci = client.post("/api/visits/check-in", json={
        "shift_id": shift["id"], "latitude": 38.79, "longitude": -121.23,
        "check_in_at": two_h_ago}).json()
    assert ci["verification_status"] == "pending"
    assert not ci["flags"]

    end = datetime.fromisoformat(shift["ends_at"])
    co = client.post(f"/api/visits/{ci['id']}/check-out",
                     json={"latitude": 38.79, "longitude": -121.23,
                           "check_out_at": (end - timedelta(minutes=5)).isoformat()}).json()
    assert co["verification_status"] == "verified"
    assert co["check_out_at"]

    # shift completed
    assert client.get(f"/api/shifts/{shift['id']}").json()["status"] == "completed"

    # timesheet auto-created
    ts = client.get("/api/timesheets", params={"caregiver_id": sample["caregiver"]["id"]}).json()
    assert len(ts) == 1
    assert ts[0]["status"] == "pending"
    assert ts[0]["hours"] >= 1.9

    # authorization units consumed
    auth = client.get(f"/api/authorizations/{sample['auth']['id']}").json()
    assert auth["units_used"] >= 1.9
    assert auth["units_remaining"] < 40.0


def test_checkin_without_authorization_flagged(client):
    # private-pay client has no authorization -> flagged visit
    cg = client.post("/api/caregivers", json={"name": "EVV CG"}).json()
    cl = client.post("/api/clients", json={"name": "Private Client", "payer": "private"}).json()
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now - timedelta(minutes=5)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat()}).json()
    ci = client.post("/api/visits/check-in", json={"shift_id": shift["id"]}).json()
    assert "no_authorization" in (ci["flags"] or [])
    co = client.post(f"/api/visits/{ci['id']}/check-out", json={}).json()
    assert co["verification_status"] == "flagged"


def test_outside_geofence_flagged(client, sample):
    shift = _make_shift(client, sample)
    # ~50 km away from the client's registered address
    ci = client.post("/api/visits/check-in", json={
        "shift_id": shift["id"], "latitude": 39.2, "longitude": -121.7}).json()
    assert "outside_geofence" in (ci["flags"] or [])


def test_double_checkin_rejected(client, sample):
    shift = _make_shift(client, sample)
    r1 = client.post("/api/visits/check-in", json={"shift_id": shift["id"]})
    assert r1.status_code == 201
    r2 = client.post("/api/visits/check-in", json={"shift_id": shift["id"]})
    assert r2.status_code in (409, 422)


def _verified_visit(client, sample):
    shift = _make_shift(client, sample)
    two_h_ago = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    ci = client.post("/api/visits/check-in",
                     json={"shift_id": shift["id"], "latitude": 38.79,
                           "longitude": -121.23, "check_in_at": two_h_ago}).json()
    end = datetime.fromisoformat(shift["ends_at"])
    return client.post(f"/api/visits/{ci['id']}/check-out",
                       json={"latitude": 38.79, "longitude": -121.23,
                             "check_out_at": (end - timedelta(minutes=5)).isoformat()}).json()


def test_evv_aggregator_batch(client, sample):
    _verified_visit(client, sample)
    start = (datetime.utcnow() - timedelta(hours=3)).isoformat()
    end = (datetime.utcnow() + timedelta(hours=3)).isoformat()
    batch = client.post("/api/evv-batches", json={
        "payer": "medicaid", "period_start": start, "period_end": end}).json()
    assert batch["visit_count"] == 1
    assert batch["status"] == "ready"
    assert batch["payload"][0]["verification_status"] == "verified"

    tx = client.post(f"/api/evv-batches/{batch['id']}/transmit").json()
    assert tx["status"] == "transmitted"


def test_aggregator_excludes_unverified(client, sample):
    # create a flagged (unverified) visit for the medicaid client
    shift = _make_shift(client, sample)
    ci = client.post("/api/visits/check-in",
                     json={"shift_id": shift["id"], "latitude": 39.2,
                           "longitude": -121.7}).json()  # outside geofence
    client.post(f"/api/visits/{ci['id']}/check-out", json={})
    start = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    end = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    batch = client.post("/api/evv-batches", json={
        "payer": "medicaid", "period_start": start, "period_end": end}).json()
    assert batch["visit_count"] == 0
