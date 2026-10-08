# HealthSoftwares

The back-office operating system for **home-care agencies** — the companies that
send caregivers into clients' homes for personal care, companionship, and
home-health visits. Think WellSky / AlayaCare / CareSmartz360: one suite that
covers scheduling, visit verification, billing, payroll, and compliance.

**Flagship module: Electronic Visit Verification (EVV).** EVV is federally
required for Medicaid-funded personal-care and home-health visits (21st Century
Cures Act): every visit must prove *who* rendered *what service*, *where*
(GPS), and *when* (timestamps). This repo implements EVV end-to-end —
This repo implements EVV enterprise-grade:

- **Visit lifecycle**: schedule → GPS/mobile/telephone/manual check-in →
  check-out, with an immutable `visit_events` audit log
- **Rules engine** (per-agency config: late/early grace periods, geofence radius):
  auto-flags late arrival, early departure, GPS-outside-geofence, missed visits
- **Exception queue** with `open → acknowledged → resolved` workflow
- **Verification status lifecycle**: `pending → verified | flagged`
- **Verification reports** (counts, exceptions by type, hours, on-time %) and
  **state-aggregator export** (CSV/JSON with member Medicaid ID, provider ID,
  service code, timestamps, GPS, verification method)
- **EVV dashboard** (`/admin/evv`): today's visits, exception queue with
  acknowledge/resolve actions, 7-day report, export links

The other ten modules ship as solid scaffold: full models + CRUD APIs + tests,
but without EVV's depth.

Built with **FastAPI + SQLAlchemy 2.x + Pydantic v2**. SQLite for local dev,
Postgres for production (`DATABASE_URL`).

---

## The 11 modules

| # | Module | What it is (domain context) | Status in v0.1 |
|---|--------|-----------------------------|----------------|
| 1 | **Training & Compliance Tracking** | Home-care aides need recurring certifications (HIPAA, CPR, dementia care). Tracks courses, assignments, due dates, completions; overdue items surface automatically. | Functional: assign → complete → overdue detection |
| 2 | **Real-Time Authorization Management** | Payers (Medicaid/MCOs) pre-authorize a number of service units per client. Check-ins validate remaining units in real time; check-outs consume them. | Functional: unit ledger, exhaustion handling |
| 3 | **Payroll Time & Attendance** | Caregivers are hourly; every verified visit generates a timesheet line with regular vs overtime hours, ready for approval. | Functional: auto-generated from EVV, approve + period summary |
| 4 | **Billing / Claims Manager** | Visits become claims billed to the payer at a contracted rate: draft → submitted → paid/denied, with denial reasons. | Functional: full claim lifecycle |
| 5 | **Scheduling & Shift Trading** | Coordinators build the weekly schedule; caregivers request shift swaps that a coordinator approves (with overlap/conflict checks). | Functional: conflict detection, trade request → approve/decline |
| 6 | **Electronic Visit Verification (EVV)** ⭐ | The compliance core: check-in/out by GPS, mobile, telephone, or manual entry; per-agency late/early grace periods and geofence radius; auto-generated exception queue (late arrival, early departure, missed visit, GPS mismatch, no-show) with acknowledge → resolve; verification report per date range. | **Flagship — enterprise-grade** |
| 7 | **EVV Aggregator** | States/payers require visit data in batched feeds. Packages verified visits for a payer + period into a transmittable batch; plus direct CSV/JSON export shaped like state aggregator feeds (member Medicaid ID, provider ID, service code, timestamps, GPS, method). | Functional: batch build → transmit; CSV/JSON export |
| 8 | **Vendor Payment & Employer Reimbursement** | Agencies buy medical supplies (vendor invoices) and reimburse caregivers for mileage/supplies. | Functional: invoice pay flow, reimbursement approve/decline |
| 9 | **Care Management** | The clinical side: per-client care plans with goals, plus progress notes from caregivers. | Scaffold: CRUD + notes (no assessments engine yet) |
| 10 | **Caregiver Rating System** | Families rate caregivers 1–5; rolling averages per caregiver feed quality oversight. | Functional: submit, average, summary |
| 11 | **Custom Automated Workflows** | No-code-style automation: *when* an event fires (visit checked in, low rating, claim denied…), *if* a condition matches, *then* run an action (flag visit, alert, log). | Functional: rule engine with test-fire endpoint |

---

## Quick start

```bash
pip install --break-system-packages --user -r requirements.txt

# seed demo data (Sunrise Home Care: week of EVV visits incl. late/early/GPS/missed exceptions)
python -m app.seed

uvicorn app.main:app --reload
```

- Admin UI: http://localhost:8000/admin
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### With Docker (Postgres)

```bash
docker compose up --build
```

The `api` service seeds demo data on first start, then serves on port 8000.

### Run tests

```bash
python -m pytest tests/ -q
```

39 tests, covering the core workflows and the EVV rules engine end-to-end.

---

## Core workflows (all genuinely functional)

**Schedule → EVV visit → payroll → billing:**
```
POST /api/shifts                          # schedule a shift (409 on caregiver overlap)
POST /api/visits/check-in                 # GPS check-in → EVV record; flags no_authorization / outside_geofence
POST /api/visits/{id}/check-out           # closes visit → verified/flagged,
                                          #   auto-creates timesheet, consumes authorized units
POST /api/claims                          # builds draft claim from verified visits
POST /api/claims/{id}/submit             # → submitted
POST /api/claims/{id}/adjudicate         # {"pay": true} → paid / {"pay": false, …} → denied
```

**EVV exception & reporting flow:**
```
GET  /api/exceptions?status=open            # exception queue
POST /api/exceptions/{id}/acknowledge       # → acknowledged
POST /api/exceptions/{id}/resolve           # → resolved (+ note)
POST /api/sweep-missed                       # flag shifts that ended with no check-in
GET  /api/report?period_start=…&period_end=… # verified/flagged/pending, exceptions by
                                             # type, hours, on-time %
GET  /api/export?format=csv&period_start=…&period_end=…  # state-aggregator feed
GET  /api/agency  /  PATCH /api/agency       # grace periods, geofence radius
```

**Shift trade:** `POST /api/trades` → `POST /api/trades/{id}/decide {"approve": true}`
reassigns the shift (with overlap checks on the receiving caregiver).

**EVV aggregation:** `POST /api/evv-batches` (verified visits only, per payer +
period) → `POST /api/evv-batches/{id}/transmit`.

**Compliance:** `POST /api/training-assignments` → `…/complete`; the overdue
list lazily marks past-due items and fires `training.overdue` workflow events.

**Ratings:** `POST /api/ratings` → `GET /api/caregivers/{id}/rating-summary`.

**Workflows:** `POST /api/workflow-rules` with `trigger_event`, `condition`,
`action`; dry-run with `POST /api/workflow-rules/test`. Two rules ship
seeded: flag visits checked in without authorization, alert on ratings < 3.

---

## Project layout

```
app/
  main.py            # FastAPI app, router wiring
  config.py          # settings (DATABASE_URL, …)
  db.py              # engine/session, init_db()
  models.py          # all SQLAlchemy models
  schemas.py         # all Pydantic v2 schemas
  workflows.py       # event → rule → action engine
  seed.py            # demo data
  admin.py           # server-rendered admin UI routes
  routers/
    people.py        # caregivers, clients, ratings
    scheduling.py    # shifts, shift trades
    evv.py           # EVV visits + aggregator batches
    finance.py       # authorizations, claims, timesheets, vendors, reimbursements
    operations.py    # training/compliance, care plans, workflow rules
templates/           # Jinja2 admin UI
tests/               # pytest suite (39 tests)
```

## Honest v0.1 scope notes

- **Auth:** none yet — single-agency deployment assumed; add auth before multi-agency use.
- **EVV aggregator transmit** marks the batch transmitted; posting the payload to
  an actual state/payer endpoint is stubbed (clearly marked in `routers/evv.py`).
- **Times are naive UTC** throughout; the admin UI labels this.
- **Schema management** is `create_all` (fine for v0.1); migrate to Alembic
  before production.
- Geofence radius is a fixed 1 km constant; per-client configuration is future work.
