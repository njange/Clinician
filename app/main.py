from datetime import date
from typing import List
import logging

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .database import engine, Base, get_db, SessionLocal
from . import models, schema, crud
from .seed import seed_clinic_doctors
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # Initialize database on startup.
        Base.metadata.create_all(bind=engine)

        # Seed data is best-effort so tests and local startup do not fail when the
        # production database is unavailable.
        db = SessionLocal()
        try:
            seed_clinic_doctors(db)
        finally:
            db.close()
    except SQLAlchemyError as exc:
        logger.warning("Skipping database initialization during startup: %s", exc)

    yield

app = FastAPI(
    title="Clinic Appointment Booking API",
    version="1.0.0",
    description="An optimized, high-concurrency healthcare slot scheduler.",
    lifespan=lifespan  # Enforces execution of the database seeding on app startup
)

@app.get("/", tags=["Root"])
def root():
    return {
        "message": "Clinic Appointment Booking API",
        "status": "healthy",
        "docs": "/docs",
    }

# Setup structured logging to capture hidden exceptions safely
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database initialization is handled during lifespan startup so importing this
# module stays side-effect free for test collection.

# GLOBAL EXCEPTION HANDLERS (No Raw Stack Traces)

@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Catches Pydantic schema validation failures and forces a clean HTTP 422 JSON response.
    """
    error_details = []
    for error in exc.errors():
        error_details.append({
            "field": " -> ".join(str(loc) for loc in error["loc"] if loc != "body"),
            "issue": error["msg"]
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "VALIDATION_FAILED", "details": error_details}
    )


@app.exception_handler(IntegrityError)
def database_integrity_exception_handler(request: Request, exc: IntegrityError):
    """
    Catches unexpected database-level unique or foreign key collisions globally (HTTP 400).
    """
    logger.error(f"Database Integrity Collision: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "DATABASE_CONFLICT",
            "message": "The operations could not be finalized due to data state conflicts or concurrent edits."
        }
    )


@app.exception_handler(SQLAlchemyError)
def general_db_exception_handler(request: Request, exc: SQLAlchemyError):
    """
    Gracefully intercepts systemic database downtime or connection errors (HTTP 500).
    """
    logger.critical(f"Systemic Database Error: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "SERVICE_UNAVAILABLE",
            "message": "An unexpected storage service exception occurred. No data was leaked."
        }
    )


@app.exception_handler(Exception)
def catch_all_exception_handler(request: Request, exc: Exception):
    """
    The ultimate fallback firewall. Ensures zero raw Python traces escape into production.
    """
    logger.critical(f"Unhandled Runtime Exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unhandled systemic error occurred. Please contact system support."
        }
    )


# CORE RESTFUL API ROUTES

@app.post(
    "/appointments", 
    response_model=schema.AppointmentResponse, 
    status_code=status.HTTP_201_CREATED
)
def create_appointment(payload: schema.AppointmentCreate, db: Session = Depends(get_db)):
    """
    Books an available 30-minute slot for a doctor[cite: 1].
    """
    try:
        return crud.book_appointment(db=db, obj_in=payload)
    except IntegrityError:
        
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This appointment slot was claimed by another patient concurrently[cite: 1]."
        )


@app.get("/doctors/{id}/availability", response_model=List[str])
def get_availability(id: int, date: date, db: Session = Depends(get_db)):
    """
    Returns all free 30-minute operational slots for a doctor on a given day[cite: 1].
    """
    return crud.get_doctor_availability(db=db, doctor_id=id, target_date=date)


@app.patch("/appointments/{id}/cancel", response_model=schema.AppointmentResponse)
def cancel_appointment(id: int, payload: schema.AppointmentCancelRequest, db: Session = Depends(get_db)):
    """
    Cancels an appointment using a mandatory reason, immediately freeing the slot[cite: 1].
    """
    appointment = db.query(models.Appointment).with_for_update().filter(
        models.Appointment.id == id
    ).first()

    if not appointment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment record not found.")

    if appointment.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This appointment has already been cancelled[cite: 1]."
        )

    appointment.status = "CANCELLED"
    appointment.cancellation_reason = payload.reason

    db.commit()
    db.refresh(appointment)
    return appointment


@app.patch("/appointments/{id}/reschedule", response_model=schema.AppointmentResponse)
def reschedule_appointment(id: int, payload: schema.AppointmentRescheduleRequest, db: Session = Depends(get_db)):
    """
    Moves an appointment to a new slot atomically[cite: 1]. 
    If validation fails or a race condition hits, changes roll back completely[cite: 1].
    """
    appointment = db.query(models.Appointment).with_for_update().filter(
        models.Appointment.id == id
    ).first()

    if not appointment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment record not found.")

    if appointment.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Cannot reschedule a cancelled appointment[cite: 1]."
        )

    # Instantiate validation payload using target attributes to satisfy validation parameters
    booking_validation_payload = schema.AppointmentCreate(
        doctor_id=appointment.doctor_id,
        patient_id=appointment.patient_id,
        slot_time=payload.new_slot_time
    )

    try:
        # Step-down the original appointment state provisionally
        appointment.status = "CANCELLED"
        appointment.cancellation_reason = f"Rescheduled to {payload.new_slot_time.isoformat()}"
        db.flush()

        # Attempt to provision the incoming booking block
        new_appointment = crud.book_appointment(db=db, obj_in=booking_validation_payload)
        return new_appointment

    except HTTPException as service_exc:
        db.rollback()
        raise service_exc
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The requested new slot was taken by a concurrent operation[cite: 1]."
        )
    

@app.get(
    "/patients/{id}/appointments", 
    response_model=schema.PatientAppointmentsResponse,
    status_code=status.HTTP_200_OK
)
def get_patient_appointments(id: int, db: Session = Depends(get_db)):
    """
    Returns all upcoming appointments for a given patient sorted by date.
    Appointments falling within 1 hour of the current time are automatically filtered out.
    """
    # Fetch filtered data from our database operations layer
    appointments = crud.get_upcoming_patient_appointments(db=db, patient_id=id)
    
    return {
        "patient_id": id,
        "upcoming_appointments": appointments
    }