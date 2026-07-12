from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schema import AppointmentCreate, AppointmentRescheduleRequest


def slot_at_least_two_hours_ahead() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def test_booking_accepts_a_slot_at_least_one_hour_ahead() -> None:
    slot_time = slot_at_least_two_hours_ahead()

    appointment = AppointmentCreate(
        doctor_id=1,
        patient_id=1,
        slot_time=slot_time,
    )

    assert appointment.slot_time == slot_time


@pytest.mark.parametrize(
    ("slot_time", "message"),
    [
        (
            (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0
            ),
            "cannot be scheduled in the past",
        ),
        (
            (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(
                second=0, microsecond=0
            ),
            "at least 1 hour in advance",
        ),
    ],
)
def test_booking_rejects_past_and_too_soon_slots(
    slot_time: datetime, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AppointmentCreate(doctor_id=1, patient_id=1, slot_time=slot_time)


@pytest.mark.parametrize(
    "slot_time",
    [
        (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        ),
        (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(
            second=0, microsecond=0
        ),
    ],
)
def test_rescheduling_applies_the_same_booking_time_rules(slot_time: datetime) -> None:
    with pytest.raises(ValidationError):
        AppointmentRescheduleRequest(new_slot_time=slot_time)
