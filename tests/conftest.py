"""Test setup: isolated SQLite DB per test session, dependency override."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app

TEST_DB = "/tmp/health_softwares_test.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

engine = create_engine(TEST_DB and f"sqlite:///{TEST_DB}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(bind=engine)


def _override_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db


@pytest.fixture()
def client():
    # fresh tables for every test
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sample(client):
    """A caregiver + client + active authorization."""
    cg = client.post("/api/caregivers", json={"name": "Test Caregiver", "phone": "+10000000001"}).json()
    cl = client.post(
        "/api/clients",
        json={"name": "Test Client", "payer": "medicaid",
              "latitude": 38.79, "longitude": -121.23},
    ).json()
    auth = client.post(
        "/api/authorizations",
        json={
            "client_id": cl["id"], "service_type": "personal_care", "payer": "medicaid",
            "units_authorized": 40.0,
            "start_date": "2026-01-01T00:00:00", "end_date": "2026-12-31T00:00:00",
        },
    ).json()
    return {"caregiver": cg, "client": cl, "auth": auth}
