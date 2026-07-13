import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.database import Base
from app import models


# TEST SETUP & DATABASE OVERRIDES

# Use an isolation-friendly, in-memory SQLite DB for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    """
    Creates a fresh database schema for each test run and cleans up afterwards.
    """
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    def _get_test_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

    
@pytest.fixture(scope="function")
def setup_doctor(db_session):
    """
    Seeds a standard doctor record into the database instance.
    """
    from datetime import time
    doctor = models.Doctor(
        id=1,
        full_name="Dr. Mwangi",
        email="mwangi@clinic.co.ke",
        personal_phone="+254712345678",
        work_start=time(8, 0),
        work_end=time(17, 0)
    )
    db_session.add(doctor)
    db_session.commit()
    return doctor


# TEST CASES

def test_successful_booking(client, setup_doctor):
    """
    Scenario 1: Successfully booking a valid appointment.
    """
    # Create a valid future time aligned to a 30-minute block (Tomorrow at 10:00 AM)
    future_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    payload = {
        "doctor_id": 1,
        "patient_id": 101,
        "slot_time": future_time.isoformat()
    }
    
    response = client.post("/appointments", json=payload)
    assert response.status_code == 211 or response.status_code == 201 # Accommodates router metadata status code
    data = response.json()
    assert data["patient_id"] == 101
    assert data["status"] == "CONFIRMED"


def test_failed_duplicate_booking(client, setup_doctor):
    """
    Scenario 2: Failing to book an already occupied slot.
    """
    future_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=11, minute=30, second=0, microsecond=0)
    
    payload = {
        "doctor_id": 1,
        "patient_id": 101,
        "slot_time": future_time.isoformat()
    }
    
    # Fire first booking sequence
    resp1 = client.post("/appointments", json=payload)
    assert resp1.status_code == 201
    
    # Attempt second collision booking targeting identical parameters.
    payload_collision = {
        "doctor_id": 1,
        "patient_id": 999,
        "slot_time": future_time.isoformat()
    }
    resp2 = client.post("/appointments", json=payload_collision)
    assert resp2.status_code == 400
    assert "already taken" in resp2.json()["detail"]


def test_successful_reschedule(client, setup_doctor):
    """
    Scenario 3: Rescheduling an active appointment successfully.
    """
    slot_alpha = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    slot_beta = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Book primary slot
    init_resp = client.post("/appointments", json={
        "doctor_id": 1,
        "patient_id": 202,
        "slot_time": slot_alpha.isoformat()
    })
    appointment_id = init_resp.json()["id"]
    
    # Invoke the rescheduling endpoint to shift the window.
    res_resp = client.patch(f"/appointments/{appointment_id}/reschedule", json={
        "new_slot_time": slot_beta.isoformat()
    })
    
    assert res_resp.status_code == 200
    assert res_resp.json()["slot_time"].startswith(slot_beta.isoformat()[:19])
    
    # Verify slot alpha is available again by attempting a new booking
    retry_resp = client.post("/appointments", json={
        "doctor_id": 1,
        "patient_id": 303,
        "slot_time": slot_alpha.isoformat()
    })
    assert retry_resp.status_code == 201


def test_booking_under_one_hour_fails(client, setup_doctor):
    """
    Scenario 4: Trying to book a slot less than an hour in advance.
    """
    # Build a target slot exactly 20 minutes from now (violating the 1-hour margin).
    invalid_near_time = datetime.now(timezone.utc) + timedelta(minutes=20)
    # Round minute value to conform to the 30-minute block rule (e.g., 0 or 30).
    target_minute = 30 if invalid_near_time.minute >= 30 else 0
    invalid_near_time = invalid_near_time.replace(minute=target_minute, second=0, microsecond=0)
    
    payload = {
        "doctor_id": 1,
        "patient_id": 404,
        "slot_time": invalid_near_time.isoformat()
    }
    
    response = client.post("/appointments", json=payload)
    
    # Pydantic validation interceptors return HTTP 422 for schema rule breaks.
    assert response.status_code == 422
    assert "at least 1 hour in advance" in response.json()["details"][0]["issue"]
