"""Enterprise EVV tests: rules engine, exceptions lifecycle, report, export."""
from datetime import datetime, timedelta


def _setup(client):
    cg = client.post("/api/caregivers", json={"name": "EVV Pro", "provider_id": "PRV-9"}).json()
    cl = client.post("/api/clients", json={
        "name": "EVV Member", "payer": "medicaid", "medicaid_id": "MCD-999",
        "latitude": 38.79, "longitude": -121.23}).json()
    client.post("/api/authorizations", json={
        "client_id": cl["id"], "service_type": "personal_care", "payer": "medicaid",
        "units_authorized": 100.0,
        "start_date": "2026-01-01T00:00:00", "end_date": "2026-12-31T00:00:00"})
    return cg, cl


def _shift(client, cl, cg, start_offset_h, duration_h=4):
    now = datetime.utcnow()
    r = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now + timedelta(hours=start_offset_h)).isoformat(),
        "ends_at": (now + timedelta(hours=start_offset_h + duration_h)).isoformat()})
    assert r.status_code == 201, r.text
    return r.json()


def _visit(client, shift, arrive_late_min=3, leave_early_min=5, lat=38.79,
           lng=-121.23, method="gps"):
    """A realistic visit: check in just after shift start, out just before end."""
    start = datetime.fromisoformat(shift["starts_at"])
    end = datetime.fromisoformat(shift["ends_at"])
    ci = client.post("/api/visits/check-in", json={
        "shift_id": shift["id"], "latitude": lat, "longitude": lng,
        "method": method,
        "check_in_at": (start + timedelta(minutes=arrive_late_min)).isoformat()})
    assert ci.status_code == 201, ci.text
    co = client.post(f"/api/visits/{ci.json()['id']}/check-out",
                     json={"latitude": lat, "longitude": lng,
                           "check_out_at": (end - timedelta(minutes=leave_early_min)).isoformat()})
    assert co.status_code == 200, co.text
    return co.json()


def test_late_arrival_exception(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)  # started 3h ago
    v = _visit(client, shift, arrive_late_min=180)
    # checked in ~3h after scheduled start -> late
    assert "late_arrival" in (v["flags"] or [])
    assert v["verification_status"] == "flagged"
    excs = client.get("/api/exceptions", params={"status": "open"}).json()
    assert any(e["exception_type"] == "late_arrival" and e["visit_id"] == v["id"]
               for e in excs)


def test_on_time_visit_has_no_late_exception(client):
    cg, cl = _setup(client)
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now - timedelta(minutes=5)).isoformat(),
        "ends_at": (now + timedelta(hours=4)).isoformat()}).json()
    v = _visit(client, shift)
    assert "late_arrival" not in (v["flags"] or [])
    assert v["verification_status"] == "verified"


def test_grace_period_is_configurable(client):
    cg, cl = _setup(client)
    client.patch("/api/agency", json={"late_grace_minutes": 240})
    assert client.get("/api/agency").json()["late_grace_minutes"] == 240
    shift = _shift(client, cl, cg, -3)
    v = _visit(client, shift, arrive_late_min=32)  # ~3h late, within 4h grace
    assert "late_arrival" not in (v["flags"] or [])
    assert v["verification_status"] == "verified"


def test_geofence_radius_is_configurable(client):
    cg, cl = _setup(client)
    client.patch("/api/agency", json={"geofence_radius_km": 100.0})
    shift = _shift(client, cl, cg, -3)
    v = _visit(client, shift, lat=39.2, lng=-121.7)  # ~50km away
    assert "outside_geofence" not in (v["flags"] or [])
    assert v["verification_status"] == "verified"


def test_early_departure_exception(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -4, duration_h=4)  # ends now
    # check in on time 4h ago, check out now (= 4h early? no: shift ends now)
    now = datetime.utcnow()
    ci_at = (now - timedelta(hours=4)).isoformat()
    ci = client.post("/api/visits/check-in", json={
        "shift_id": shift["id"], "latitude": 38.79, "longitude": -121.23,
        "check_in_at": ci_at}).json()
    # check out 60 min before scheduled end
    co = client.post(f"/api/visits/{ci['id']}/check-out", json={
        "check_out_at": (now - timedelta(minutes=60)).isoformat()}).json()
    assert "early_departure" in (co["flags"] or [])
    excs = client.get("/api/exceptions", params={"exception_type": "early_departure"}).json()
    assert any(e["visit_id"] == co["id"] for e in excs)


def test_gps_mismatch_exception(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)
    v = _visit(client, shift, lat=39.2, lng=-121.7)
    assert "outside_geofence" in (v["flags"] or [])
    excs = client.get("/api/exceptions", params={"exception_type": "gps_mismatch"}).json()
    assert any(e["visit_id"] == v["id"] for e in excs)


def test_missed_visit_sweep(client):
    cg, cl = _setup(client)
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now - timedelta(hours=5)).isoformat(),
        "ends_at": (now - timedelta(hours=1)).isoformat()}).json()
    r = client.post("/api/sweep-missed").json()
    assert r["missed_flagged"] == 1
    excs = client.get("/api/exceptions", params={"exception_type": "missed_visit"}).json()
    assert any(e["shift_id"] == shift["id"] for e in excs)
    assert client.get(f"/api/shifts/{shift['id']}").json()["status"] == "missed"
    # idempotent: second sweep finds nothing new
    assert client.post("/api/sweep-missed").json()["missed_flagged"] == 0


def test_exception_acknowledge_resolve_lifecycle(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)
    v = _visit(client, shift, arrive_late_min=32)  # late -> exception
    exc = next(e for e in client.get("/api/exceptions",
                                     params={"status": "open"}).json()
               if e["visit_id"] == v["id"])
    ack = client.post(f"/api/exceptions/{exc['id']}/acknowledge").json()
    assert ack["status"] == "acknowledged"
    assert ack["acknowledged_at"]
    # double-acknowledge rejected
    assert client.post(f"/api/exceptions/{exc['id']}/acknowledge").status_code == 422
    res = client.post(f"/api/exceptions/{exc['id']}/resolve",
                      json={"resolution_note": "traffic jam, verified by phone"}).json()
    assert res["status"] == "resolved"
    assert res["resolution_note"] == "traffic jam, verified by phone"


def test_visit_events_audit_log(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)
    v = _visit(client, shift, method="mobile")
    events = client.get(f"/api/visits/{v['id']}/events").json()
    assert [e["event_type"] for e in events] == ["check_in", "check_out"]
    assert all(e["method"] == "mobile" for e in events)


def test_invalid_method_rejected(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, 1)
    r = client.post("/api/visits/check-in",
                    json={"shift_id": shift["id"], "method": "telepathy"})
    assert r.status_code == 422


def test_verification_report(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)
    _visit(client, shift, arrive_late_min=32)  # late -> flagged
    now = datetime.utcnow()
    rep = client.get("/api/report", params={
        "period_start": (now - timedelta(hours=6)).isoformat(),
        "period_end": (now + timedelta(hours=6)).isoformat()}).json()
    assert rep["total_visits"] == 1
    assert rep["flagged"] == 1
    assert rep["verified"] == 0
    assert rep["exceptions_by_type"].get("late_arrival") == 1
    assert rep["open_exceptions"] >= 1
    assert rep["total_hours"] > 0


def test_export_json_and_csv(client):
    cg, cl = _setup(client)
    shift = _shift(client, cl, cg, -3)
    _visit(client, shift, method="telephone")
    now = datetime.utcnow()
    params = {"period_start": (now - timedelta(hours=6)).isoformat(),
              "period_end": (now + timedelta(hours=6)).isoformat()}

    rows = client.get("/api/export", params={**params, "format": "json"}).json()
    assert len(rows) == 1
    row = rows[0]
    for field in ("visit_id", "member_medicaid_id", "provider_id", "service_code",
                  "check_in", "check_out", "check_in_lat", "check_in_lng",
                  "verification_method", "verification_status"):
        assert field in row, field
    assert row["member_medicaid_id"] == "MCD-999"
    assert row["provider_id"] == "PRV-9"
    assert row["verification_method"] == "telephone"

    csv_resp = client.get("/api/export", params={**params, "format": "csv"})
    assert csv_resp.status_code == 200
    assert "member_medicaid_id" in csv_resp.text
    assert "MCD-999" in csv_resp.text

    assert client.get("/api/export",
                      params={**params, "format": "xml"}).status_code == 422
