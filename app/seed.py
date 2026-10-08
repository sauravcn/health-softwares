"""Seed demo data: `python -m app.seed`.

Builds "Sunrise Home Care" with a full week of EVV visit history so the
exception queue, verification report, and export show real data on first run:

* on-time verified visit            * late arrival (> 15 min grace)
* early departure (> 15 min grace)   * GPS mismatch (outside 1 km geofence)
* missed visit (no check-in)         * no-authorization (private-pay client)
* one upcoming shift today (for the EVV dashboard)
"""
from datetime import datetime, timedelta

from . import models
from .db import SessionLocal, init_db
from .routers.evv import do_check_in, do_check_out, get_agency, sweep_missed_visits

NOW = datetime.utcnow()


def _shift(db, client, caregiver, start, hours, service_type="personal_care"):
    s = models.Shift(client_id=client.id,
                     caregiver_id=caregiver.id if caregiver else None,
                     starts_at=start, ends_at=start + timedelta(hours=hours),
                     service_type=service_type,
                     status="open" if caregiver is None else "scheduled")
    db.add(s)
    db.flush()
    return s


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(models.Caregiver).first():
            print("database already seeded; skipping")
            return

        agency = get_agency(db)
        agency.name = "Sunrise Home Care"
        agency.geofence_radius_km = 1.0
        agency.late_grace_minutes = 15
        agency.early_departure_grace_minutes = 15

        caregivers = [
            models.Caregiver(name="Maria Santos", phone="+19165550101",
                             email="maria@example.com",
                             hire_date=NOW - timedelta(days=400),
                             skills=["CNA", "dementia"], provider_id="PRV-1001"),
            models.Caregiver(name="James Okafor", phone="+19165550102",
                             email="james@example.com",
                             hire_date=NOW - timedelta(days=200),
                             skills=["CNA"], provider_id="PRV-1002"),
            models.Caregiver(name="Linda Chen", phone="+19165550103",
                             email="linda@example.com",
                             hire_date=NOW - timedelta(days=90),
                             skills=["HHA", "hospice"], provider_id="PRV-1003"),
            models.Caregiver(name="Robert Diaz", phone="+19165550104",
                             email="robert@example.com",
                             hire_date=NOW - timedelta(days=30),
                             skills=["HHA"], provider_id="PRV-1004"),
        ]
        db.add_all(caregivers)
        clients = [
            models.Client(name="Eleanor Rigby", address="123 Maple St, Rocklin, CA",
                          phone="+19165550201", payer="medicaid", medicaid_id="MCD-500101",
                          latitude=38.7900, longitude=-121.2350),
            models.Client(name="Harold Finch", address="456 Oak Ave, Roseville, CA",
                          phone="+19165550202", payer="medicaid", medicaid_id="MCD-500102",
                          latitude=38.7521, longitude=-121.2880),
            models.Client(name="Agnes Porter", address="789 Pine Ln, Lincoln, CA",
                          phone="+19165550203", payer="private", medicaid_id=None,
                          latitude=38.8916, longitude=-121.2930),
        ]
        db.add_all(clients)
        db.flush()
        maria, james, linda, robert = caregivers
        eleanor, harold, agnes = clients

        for client in (eleanor, harold):
            db.add(models.Authorization(
                client_id=client.id, service_type="personal_care", payer="medicaid",
                units_authorized=40.0, units_used=0.0, unit_label="hours",
                start_date=NOW - timedelta(days=30), end_date=NOW + timedelta(days=60),
                status="active"))
        db.flush()

        def at_home(client):
            return {"latitude": client.latitude, "longitude": client.longitude}

        # Day -6: clean on-time visit -> verified
        s = _shift(db, eleanor, maria, NOW - timedelta(days=6, hours=5), 4)
        v = do_check_in(db, s, method="gps", check_in_at=s.starts_at + timedelta(minutes=3),
                        **at_home(eleanor))
        do_check_out(db, v, check_out_at=s.ends_at - timedelta(minutes=5),
                     **at_home(eleanor))

        # Day -5: late arrival (32 min > 15 min grace) -> late_arrival exception
        s = _shift(db, harold, james, NOW - timedelta(days=5, hours=5), 4)
        v = do_check_in(db, s, method="mobile",
                        check_in_at=s.starts_at + timedelta(minutes=32),
                        **at_home(harold))
        do_check_out(db, v, check_out_at=s.ends_at - timedelta(minutes=2),
                     **at_home(harold))

        # Day -4: early departure (50 min > 15 min grace) -> early_departure
        s = _shift(db, eleanor, linda, NOW - timedelta(days=4, hours=5), 4)
        v = do_check_in(db, s, method="gps",
                        check_in_at=s.starts_at + timedelta(minutes=5),
                        **at_home(eleanor))
        do_check_out(db, v, check_out_at=s.ends_at - timedelta(minutes=50),
                     **at_home(eleanor))

        # Day -3: GPS 8 km away -> gps_mismatch exception
        s = _shift(db, harold, maria, NOW - timedelta(days=3, hours=5), 4)
        v = do_check_in(db, s, method="gps",
                        check_in_at=s.starts_at + timedelta(minutes=4),
                        latitude=harold.latitude + 0.07, longitude=harold.longitude)
        do_check_out(db, v, check_out_at=s.ends_at - timedelta(minutes=4),
                     latitude=harold.latitude + 0.07, longitude=harold.longitude)

        # Day -2: shift ended with no check-in -> missed_visit (via sweep)
        _shift(db, eleanor, james, NOW - timedelta(days=2, hours=5), 4)

        # Day -1: private-pay client, no authorization -> flagged, billed private
        s = _shift(db, agnes, linda, NOW - timedelta(days=1, hours=5), 3)
        v = do_check_in(db, s, method="telephone",
                        check_in_at=s.starts_at + timedelta(minutes=6),
                        **at_home(agnes))
        do_check_out(db, v, check_out_at=s.ends_at - timedelta(minutes=6),
                     **at_home(agnes))

        # Today: upcoming scheduled shift (shows on the EVV dashboard)
        start = (NOW + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        _shift(db, eleanor, maria, start, 4)

        missed = sweep_missed_visits(db)

        # ---- supporting scaffold data (other modules) ----
        courses = [
            models.TrainingCourse(title="HIPAA Privacy & Security", required=True,
                                  validity_days=365),
            models.TrainingCourse(title="Fall Prevention Basics", required=True,
                                  validity_days=365),
            models.TrainingCourse(title="Dementia Care Essentials", required=False,
                                  validity_days=730),
        ]
        db.add_all(courses)
        db.flush()
        db.add(models.TrainingAssignment(
            caregiver_id=maria.id, course_id=courses[0].id,
            assigned_date=NOW - timedelta(days=60), due_date=NOW - timedelta(days=30),
            completed_date=NOW - timedelta(days=35), status="completed"))
        db.add(models.TrainingAssignment(
            caregiver_id=robert.id, course_id=courses[0].id,
            assigned_date=NOW - timedelta(days=45), due_date=NOW - timedelta(days=5),
            status="assigned"))  # overdue on read

        db.add(models.Rating(caregiver_id=maria.id, client_id=eleanor.id,
                             score=5, comment="Maria is wonderful with Mom."))
        db.add(models.Rating(caregiver_id=maria.id, client_id=agnes.id,
                             score=4, comment="Very reliable."))

        vendor = models.Vendor(name="MediSupply Co.", vendor_type="medical_supplies",
                               contact="orders@medisupply.example.com")
        db.add(vendor)
        db.flush()
        db.add(models.VendorInvoice(vendor_id=vendor.id, amount=412.50,
                                    description="Gloves, sanitizer, briefs",
                                    due_date=NOW + timedelta(days=14), status="pending"))

        plan = models.CarePlan(client_id=eleanor.id, start_date=NOW - timedelta(days=20),
                               goals=["Maintain independent ambulation with walker",
                                      "Medication reminders twice daily"],
                               status="active")
        db.add(plan)
        db.flush()
        db.add(models.CareNote(care_plan_id=plan.id, caregiver_id=maria.id,
                               note="Client walked 200 ft with walker, no shortness of breath.",
                               note_type="progress"))

        db.add(models.WorkflowRule(
            name="Flag visits missing authorization",
            trigger_event="visit.checked_in",
            condition={"field": "flags", "contains": "no_authorization"},
            action={"type": "alert",
                    "message": "Visit checked in with no active authorization — review before billing."},
            active=True))
        db.add(models.WorkflowRule(
            name="Alert on low rating",
            trigger_event="rating.submitted",
            condition={"field": "score", "lt": 3},
            action={"type": "alert",
                    "message": "Caregiver received a rating below 3 — follow up with family."},
            active=True))

        db.commit()
        n_exc = db.query(models.VisitException).count()
        print(f"seeded: week of EVV visits (1 verified, 4 flagged, {missed} missed), "
              f"{n_exc} exceptions, 4 caregivers, 3 clients + scaffold data")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
