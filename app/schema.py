from datetime import datetime, time, timedelta, timezone
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator


def validate_bookable_slot(slot_time: datetime) -> datetime:
    """Normalize a slot to UTC and enforce the minimum booking lead time."""
    if slot_time.tzinfo is None:
        slot_time = slot_time.replace(tzinfo=timezone.utc)
    else:
        slot_time = slot_time.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    if slot_time < now:
        raise ValueError("Appointment slot cannot be scheduled in the past.")

    if slot_time < now + timedelta(hours=1):
        raise ValueError("Appointments must be booked at least 1 hour in advance.")

    if (
        slot_time.minute not in (0, 30)
        or slot_time.second != 0
        or slot_time.microsecond != 0
    ):
        raise ValueError(
            "Appointments must be aligned precisely to a 30-minute block (e.g., :00 or :30)."
        )

    return slot_time


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
    personal_phone: str = Field(..., max_length=20)


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
        description="Desired slot. Must be an explicitly UTC-localized ISO timestamp."
    )


class AppointmentCreate(AppointmentBase):
    patient_id: int

    @field_validator("slot_time")
    @classmethod
    def validate_slot_rules(cls, v: datetime) -> datetime:
        return validate_bookable_slot(v)


class AppointmentRescheduleRequest(BaseModel):
    new_slot_time: datetime = Field(
        ...,
        description="Replacement slot, as an ISO 8601 timestamp.",
    )

    @field_validator("new_slot_time")
    @classmethod
    def validate_new_slot_rules(cls, v: datetime) -> datetime:
        return validate_bookable_slot(v)


class AppointmentCancelRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Reason required for tracking cancellations.")


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
