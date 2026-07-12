from datetime import date
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .database import engine, Base, get_db
from . import models, schema, crud

# Automatically generate tables on startup if they don't exist
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Clinic Appointment Booking API",
    version="1.0.0",
    description="An optimized, high-concurrency healthcare slot scheduler compliant with Kenya ODPC principles."
)

# POST /appointments (Booking)

@app.post(
    "/appointments", 
    response_model=schema.AppointmentPublic, 
    status_code=status.HTTP_201_CREATED
)
def create_appointment(payload: schema.AppointmentCreate, db: Session = Depends(get_db)):
    """
    Books an available 30-minute slot for a doctor.
    """
    try:
        return crud.book_appointment(db=db, obj_in=payload)
    except IntegrityError:
        # Handles database-level unique constraint triggers if row locks are bypassed
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This appointment slot was claimed by another patient concurrently."
        )


# GET /doctors/{id}/availability

@app.get("/doctors/{id}/availability", response_model=List[str])
def get_availability(id: int, date: date, db: Session = Depends(get_db)):
    """
    Returns all free 30-minute operational slots for a doctor on a given day.
    """
    return crud.get_doctor_availability(db=db, doctor_id=id, target_date=date)


# PATCH /appointments/{id}/cancel

@app.patch("/appointments/{id}/cancel", response_model=schema.AppointmentPublic)
def cancel_appointment(id: int, payload: schema.AppointmentCancelRequest, db: Session = Depends(get_db)):
    """
    Cancels an appointment using a mandatory reason, immediately freeing the slot.
    """
    # Fetch the appointment record using row locking to secure safe status mutations
    appointment = db.query(models.Appointment).with_for_update().filter(
        models.Appointment.id == id
    ).first()

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment record not found")

    if appointment.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This appointment has already been cancelled."
        )

    appointment.status = "CANCELLED"
    appointment.cancellation_reason = payload.reason

    db.commit()
    db.refresh(appointment)
    return appointment


# PATCH /appointments/{id}/reschedule (Atomic)

@app.patch("/appointments/{id}/reschedule", response_model=schema.AppointmentPublic)
def reschedule_appointment(id: int, payload: schema.AppointmentRescheduleRequest, db: Session = Depends(get_db)):
    """
    Moves an appointment to a new slot atomically[cite: 1]. 
    If validation fails or a race condition hits, changes roll back completely[cite: 1].
    """
    # 1. Acquire explicit row-level lock on the original booking record
    appointment = db.query(models.Appointment).with_for_update().filter(
        models.Appointment.id == id
    ).first()

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment record not found")

    if appointment.status == "CANCELLED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Cannot reschedule a cancelled appointment[cite: 1]."
        )

    # 2. Extract context details to prepare fresh booking validation payload
    booking_validation_payload = schema.AppointmentCreate(
        doctor_id=appointment.doctor_id,
        patient_id=appointment.patient_id,
        slot_time=payload.new_slot_time
    )

    try:
        # 3. Transition original slot to CANCELLED state within the current active transaction
        appointment.status = "CANCELLED"
        appointment.cancellation_reason = f"Rescheduled to {payload.new_slot_time.isoformat()}"
        db.flush()  # Flushes changes to free up the slot index conditionally without committing

        # 4. Attempt to insert the fresh scheduling replacement row cleanly[cite: 1]
        new_appointment = crud.book_appointment(db=db, obj_in=booking_validation_payload)
        
        # 5. If everything passes, finalize and commit both changes together
        return new_appointment

    except HTTPException as service_exc:
        # Catch and rollback business logic exceptions (e.g. out of hours, slot taken)[cite: 1]
        db.rollback()
        raise service_exc
    except IntegrityError:
        # Catch and rollback indexing collisions if another thread beats us to the new slot[cite: 1]
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The requested new slot was taken by a concurrent operation[cite: 1]."
        )