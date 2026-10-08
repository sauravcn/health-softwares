"""Billing/claims and payroll tests."""
from datetime import datetime, timedelta


def _verified_visit(client, sample):
    cg, cl = sample["caregiver"], sample["client"]
    now = datetime.utcnow()
    shift = client.post("/api/shifts", json={
        "client_id": cl["id"], "caregiver_id": cg["id"],
        "starts_at": (now - timedelta(hours=3)).isoformat(),
        "ends_at": (now + timedelta(hours=4)).isoformat()}).json()
    start = datetime.fromisoformat(shift["starts_at"])
    ci = client.post("/api/visits/check-in",
                     json={"shift_id": shift["id"], "latitude": 38.79,
                           "longitude": -121.23,
                           "check_in_at": (start + timedelta(minutes=3)).isoformat()}).json()
    end = datetime.fromisoformat(shift["ends_at"])
    return client.post(f"/api/visits/{ci['id']}/check-out",
                       json={"latitude": 38.79, "longitude": -121.23,
                             "check_out_at": (end - timedelta(minutes=5)).isoformat()}).json()


def _claim_body(sample):
    now = datetime.utcnow()
    return {
        "client_id": sample["client"]["id"],
        "authorization_id": sample["auth"]["id"],
        "payer": "medicaid",
        "service_date_from": (now - timedelta(hours=4)).isoformat(),
        "service_date_to": (now + timedelta(hours=4)).isoformat(),
        "rate_per_unit": 32.0,
    }


def test_claim_lifecycle_paid(client, sample):
    _verified_visit(client, sample)
    claim = client.post("/api/claims", json=_claim_body(sample)).json()
    assert claim["status"] == "draft"
    assert claim["units"] >= 0
    assert claim["amount"] == round(claim["units"] * 32.0, 2)

    submitted = client.post(f"/api/claims/{claim['id']}/submit").json()
    assert submitted["status"] == "submitted"
    assert submitted["submitted_at"]

    paid = client.post(f"/api/claims/{claim['id']}/adjudicate",
                       json={"pay": True}).json()
    assert paid["status"] == "paid"
    assert paid["adjudicated_at"]


def test_claim_denied_with_reason(client, sample):
    _verified_visit(client, sample)
    claim = client.post("/api/claims", json=_claim_body(sample)).json()
    client.post(f"/api/claims/{claim['id']}/submit")
    denied = client.post(f"/api/claims/{claim['id']}/adjudicate",
                         json={"pay": False, "denial_reason": "auth exhausted"}).json()
    assert denied["status"] == "denied"
    assert denied["denial_reason"] == "auth exhausted"


def test_empty_claim_cannot_submit(client, sample):
    claim = client.post("/api/claims", json=_claim_body(sample)).json()
    assert claim["units"] == 0
    r = client.post(f"/api/claims/{claim['id']}/submit")
    assert r.status_code == 422


def test_timesheet_approve_and_payroll_summary(client, sample):
    _verified_visit(client, sample)
    cg_id = sample["caregiver"]["id"]
    ts = client.get("/api/timesheets", params={"caregiver_id": cg_id}).json()[0]
    approved = client.post(f"/api/timesheets/{ts['id']}/approve",
                           json={"approve": True, "pay_rate": 22.5}).json()
    assert approved["status"] == "approved"
    assert approved["pay_rate"] == 22.5

    now = datetime.utcnow()
    summary = client.get("/api/payroll/summary", params={
        "caregiver_id": cg_id,
        "period_start": (now - timedelta(days=1)).isoformat(),
        "period_end": (now + timedelta(days=1)).isoformat()}).json()
    assert summary["entries"] == 1
    assert summary["total_hours"] >= 0


def test_vendor_invoice_pay_flow(client):
    v = client.post("/api/vendors", json={"name": "Test Supplier"}).json()
    inv = client.post("/api/vendor-invoices",
                      json={"vendor_id": v["id"], "amount": 100.0,
                            "description": "supplies"}).json()
    assert inv["status"] == "pending"
    paid = client.post(f"/api/vendor-invoices/{inv['id']}/pay").json()
    assert paid["status"] == "paid"
    assert paid["paid_at"]


def test_reimbursement_decide_flow(client, sample):
    r = client.post("/api/reimbursements", json={
        "caregiver_id": sample["caregiver"]["id"], "amount": 42.17,
        "category": "mileage"}).json()
    assert r["status"] == "submitted"
    decided = client.post(f"/api/reimbursements/{r['id']}/decide",
                          json={"approve": True}).json()
    assert decided["status"] == "approved"
