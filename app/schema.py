from datetime import datetime, time, timezone
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator


# 1. DOCTOR SCHEMAS

class DoctorBase(BaseModel):
    full_name: str = Field(..., max_length=100, examples=["Dr. Mwangi"])
    work_start: time = Field(..., examples=["08:00:00"])
    work_end: time = Field(..., examples=["17:00:00"])


class DoctorCreate(DoctorBase):
    """
    Used ONLY when registering a doctor. Contains highly sensitive PII fields.
    """
    email: EmailStr
    personal_phone: str = Field(..., max_length=20, examples=["+254712345678"])


class DoctorPublic(DoctorBase):
    """
    PUBLIC RESPONSE SCHEMA. 
    Strictly masks and isolates PII (personal_phone, email) from exposure.
    """
    id: int

    class Config:
        from_attributes = True


# 2. APPOINTMENT SCHEMAS

class AppointmentBase(BaseModel):
    doctor_id: int
    slot_time: datetime = Field(
        ..., 
        description="Desired slot. Must be an explicitly UTC-localized ISO timestamp.",
        examples=["2026-07-15T10:30:00Z"]
    )


class AppointmentCreate(AppointmentBase):
    patient_id: int

    @field_validator("slot_time")
    @classmethod
    def validate_slot_rules(cls, v: datetime) -> datetime:
        # 1. Force conversion or verification of UTC zone to avoid offset mismatch crashes
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        else:
            v = v.astimezone(timezone.utc)

        # Grab current time explicitly in UTC format
        current_time = datetime.now(timezone.utc)

        # 2. Core Validation: Ensure the appointment slot is not in the past
        if v < current_time:
            raise ValueError("Appointment slot cannot be scheduled in the past.")

        # 3. Bonus Validation: Prevention of bookings within 1 hour of now
        time_difference = v - current_time
        if time_difference.total_seconds() < 3600:
            raise ValueError("Appointments must be booked at least 1 hour in advance.")

        # 4. Strict Grid Constraint: Enforce fixed 30-minute clinic slots[cite: 1]
        if v.minute not in (0, 30) or v.second != 0 or v.microsecond != 0:
            raise ValueError("Appointments must be aligned precisely to a 30-minute block (e.g., :00 or :30).")

        return v


class AppointmentCancelRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Reason required for tracking cancellations.")


class AppointmentRescheduleRequest(BaseModel):
    """
    Explicit schema for handling rescheduling requests.
    Enforces identical safety checks as a fresh booking before database processing[cite: 1].
    """
    new_slot_time: datetime = Field(..., description="Target slot time in UTC format.")

    @field_validator("new_slot_time")
    @classmethod
    def validate_reschedule_slot(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        else:
            v = v.astimezone(timezone.utc)

        current_time = datetime.now(timezone.utc)

        if v < current_time:
            raise ValueError("The target rescheduling slot cannot be in the past.")
            
        if (v - current_time).total_seconds() < 3600:
            raise ValueError("Rescheduled appointments must be locked in at least 1 hour in advance.")
            
        if v.minute not in (0, 30) or v.second != 0 or v.microsecond != 0:
            raise ValueError("Rescheduled appointments must match a precise 30-minute block.")
            
        return v


class AppointmentResponse(BaseModel):
    """
    Outward-facing confirmation payload. Returns minimal reference details 
    and links to the safely masked DoctorPublic schema.
    """
    id: int
    patient_id: int
    slot_time: datetime
    status: str
    cancellation_reason: Optional[str] = None
    doctor: DoctorPublic  # Nesting public info to guarantee PII protection[cite: 1]

    class Config:
        from_attributes = True


# 3. AVAILABILITY SCHEMAS


class AvailabilityResponse(BaseModel):
    date: str
    doctor_id: int
    available_slots: List[datetime]

    class Config:
        from_attributes = True