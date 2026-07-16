import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.database import Base
from app import models

# Simple test auth headers placeholder to satisfy endpoints expecting auth
auth_headers = {"Authorization": "Bearer test-token"}


# TEST SETUP & DATABASE OVERRIDES

# Use an isolation-friendly, in-memory SQLite DB for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def test_register_user(client):
    response = client.post(
        "/auth/register",
        json={
            "full_name": "James",
            "email": "james@test.com",
            "password": "password123",
        },
    )

    assert response.status_code == 201

def test_login_success(client):
    client.post(
        "/auth/register",
        json={
            "full_name": "James",
            "email": "james@test.com",
            "password": "password123",
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "james@test.com",
            "password": "password123",
        },
    )

    assert response.status_code == 200
    assert "access_token" in response.json()

def test_login_invalid_password(client):
    client.post(
        "/auth/register",
        json={
            "full_name": "James",
            "email": "james@test.com",
            "password": "password123",
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "james@test.com",
            "password": "wrongpassword",
        },
    )

    assert response.status_code == 401

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
def test_user(db_session):
    """
    Creates a patient user for authenticated endpoint testing.
    """
    from app.auth.security import hash_password

    user = models.User(
        full_name="Test Patient",
        email="patient@test.com",
        password_hash=hash_password("password123"),
        role=models.UserRole.PATIENT,
    )

    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    return user


@pytest.fixture(scope="function")
def auth_headers(client, test_user):
    """
    Logs in the test user and returns Authorization headers.
    """
    response = client.post(
        "/auth/login",
        json={
            "email": "patient@test.com",
            "password": "password123",
        },
    )

    assert response.status_code == 200

    token = response.json()["access_token"]

    return {
        "Authorization": f"Bearer {token}"
    }
    
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

def test_successful_booking(client, setup_doctor, auth_headers):
    """
    Scenario 1: Successfully booking a valid appointment.
    """

    future_time = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )

    payload = {
        "doctor_id": 1,
        "slot_time": future_time.isoformat(),
    }

    response = client.post(
        "/appointments",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 201

    data = response.json()

    assert data["patient_id"] == 1
    assert data["status"] == "CONFIRMED"


def test_failed_duplicate_booking(client, setup_doctor, auth_headers):
    """
    Scenario 2: Failing to book an already occupied slot.
    """

    future_time = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(
        hour=11,
        minute=30,
        second=0,
        microsecond=0,
    )

    payload = {
        "doctor_id": 1,
        "slot_time": future_time.isoformat(),
    }

    resp1 = client.post(
        "/appointments",
        json=payload,
        headers=auth_headers,
    )

    assert resp1.status_code == 201

    resp2 = client.post(
        "/appointments",
        json=payload,
        headers=auth_headers,
    )

    assert resp2.status_code == 400
    assert "already taken" in resp2.json()["detail"]


def test_successful_reschedule(client, setup_doctor, auth_headers):
    """
    Scenario 3: Rescheduling an appointment.
    """

    slot_alpha = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(
        hour=14,
        minute=0,
        second=0,
        microsecond=0,
    )

    slot_beta = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(
        hour=15,
        minute=30,
        second=0,
        microsecond=0,
    )

    init_resp = client.post(
        "/appointments",
        json={
            "doctor_id": 1,
            "slot_time": slot_alpha.isoformat(),
        },
        headers=auth_headers,
    )

    appointment_id = init_resp.json()["id"]

    res_resp = client.patch(
        f"/appointments/{appointment_id}/reschedule",
        json={
            "new_slot_time": slot_beta.isoformat(),
        },
        headers=auth_headers,
    )

    assert res_resp.status_code == 200

    retry_resp = client.post(
        "/appointments",
        json={
            "doctor_id": 1,
            "slot_time": slot_alpha.isoformat(),
        },
        headers=auth_headers,
    )

    assert retry_resp.status_code == 201

def test_booking_under_one_hour_fails(
    client,
    setup_doctor,
    auth_headers,
    monkeypatch,
):
    """
    Scenario 4: Booking less than one hour ahead.
    """

    frozen_now = datetime(
        2026,
        7,
        15,
        10,
        0,
        0,
        tzinfo=timezone.utc,
    )

    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr("app.schema.datetime", MockDatetime)
    monkeypatch.setattr("app.crud.datetime", MockDatetime)

    invalid_time = datetime(
        2026,
        7,
        15,
        10,
        30,
        tzinfo=timezone.utc,
    )

    response = client.post(
        "/appointments",
        json={
            "doctor_id": 1,
            "slot_time": invalid_time.isoformat(),
        },
        headers=auth_headers,
    )

    assert response.status_code == 422