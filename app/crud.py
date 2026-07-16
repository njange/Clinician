from datetime import datetime, date, timedelta, time, timezone
from typing import List, Set
from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status

from . import models, schema

# ==========================================
# CORE REQ: DYNAMIC SLOT SLICING LOGIC
# ==========================================

def calculate_slots_for_day(work_start: time, work_end: time, target_date: date) -> List[datetime]:
    """
    Dynamically slices a doctor's working hours into sequential 30-minute intervals
    for a given calendar day, returning absolute UTC datetimes.
    """
    slots = []
    # Combine date and time to construct exact datetime bounds
    current_dt = datetime.combine(target_date, work_start)
    end_dt = datetime.combine(target_date, work_end)
    
    # Loop sequentially in 30-minute increments
    while current_dt + timedelta(minutes=30) <= end_dt:
        slots.append(current_dt)
        current_dt += timedelta(minutes=30)
        
    return slots


def get_doctor_availability(db: Session, doctor_id: int, target_date: date) -> List[str]:
    """
    Returns all unbooked, available 30-minute slot strings for a doctor on a specific date.
    """
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
        
    # 1. Generate the absolute master grid layout for this doctor's working hours
    all_slots = calculate_slots_for_day(doctor.work_start, doctor.work_end, target_date)
    
    # 2. Fetch all active, non-cancelled bookings assigned to this doctor for the day
    start_of_day = datetime.combine(target_date, time.min)
    end_of_day = datetime.combine(target_date, time.max)
    
    booked_appointments = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.slot_time >= start_of_day,
        models.Appointment.slot_time <= end_of_day,
        models.Appointment.status != "CANCELLED"
    ).all()
    
    # Extract booked slot datetimes into a lookup set for O(1) checking efficiency
    booked_times: Set[datetime] = {appt.slot_time for appt in booked_appointments}
    
    # 3. Filter out booked items from the master grid to determine available slots
    available_slots = [
        slot.isoformat() + "Z" for slot in all_slots if slot not in booked_times
    ]
    
    return available_slots


# ==========================================
# ATOMIC BOOKING LOGIC WITH CONCURRENCY PROTECTION
# ==========================================

def book_appointment(
    db: Session,
    obj_in: schema.AppointmentCreate,
    patient_id: int,
) -> models.Appointment:
    """
    Executes an atomic check-and-insert transaction utilizing a pessimistic row-level 
    lock on the doctor's record to fully mitigate concurrent scheduling race conditions.
    """
    # CRITICAL FIX: Lock the Doctor record using .with_for_update() to force concurrent 
    # booking requests targeting the same doctor to queue up serially.
    doctor = db.query(models.Doctor).with_for_update().filter(
        models.Doctor.id == obj_in.doctor_id
    ).first()
    
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # 1. Validate that the chosen slot falls strictly within operational hours
    slot_time_only = obj_in.slot_time.time()
    slot_end_time = (obj_in.slot_time + timedelta(minutes=30)).time()
    
    if slot_time_only < doctor.work_start or slot_end_time > doctor.work_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested slot falls outside the doctor's configured working hours."
        )

    # 2. Evaluate if the selected slot time is already taken by an active booking
    is_already_booked = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == obj_in.doctor_id,
        models.Appointment.slot_time == obj_in.slot_time,
        models.Appointment.status != "CANCELLED"
    ).first()

    if is_already_booked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This appointment slot is already taken."
        )

    # 3. Create and commit the validated appointment record securely
    db_appointment = models.Appointment(
    doctor_id=obj_in.doctor_id,
    patient_id=patient_id,
    slot_time=obj_in.slot_time,
    status="CONFIRMED",
    )
    
    db.add(db_appointment)
    db.commit()
    db.refresh(db_appointment)
    
    return db_appointment

def get_upcoming_patient_appointments(db: Session, patient_id: int) -> List[models.Appointment]:
    """
    Retrieves all active upcoming appointments for a specific patient.
    Enforces the bonus condition: only returns appointments scheduled at least 1 hour from now,
    sorted sequentially by date and time.
    """
    # Define our 1-hour safety baseline in UTC
    one_hour_from_now = datetime.now(timezone.utc) + timedelta(hours=1)

    return db.query(models.Appointment).filter(
        models.Appointment.patient_id == patient_id,
        models.Appointment.status != "CANCELLED",
        models.Appointment.slot_time >= one_hour_from_now  # Enforces the 1-hour booking horizon protection
    ).order_by(models.Appointment.slot_time.asc()).all()