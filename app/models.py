from sqlalchemy import Column, Integer, String, Time, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import relationship
from .database import Base

class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(100), nullable=False)
    
    # Internal infrastructure fields. These are tracked internally but 
    # must be completely stripped out in Pydantic schemas to avoid public PII exposure.
    email = Column(String(100), nullable=False)
    personal_phone = Column(String(20), nullable=False)
    
    # Working hours bounds (Stored natively as Time fields without timezones)
    work_start = Column(Time, nullable=False)
    work_end = Column(Time, nullable=False)

    # Relationship linking back to booked/cancelled appointments
    appointments = relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(Integer, nullable=False, index=True)
    
    # Crucial: Enforced explicitly as DateTime with Timezone (TIMESTAMPTZ in Postgres)
    # This prevents varying localized engine/server time offset bugs.
    slot_time = Column(DateTime(timezone=True), nullable=False)
    
    # States: "PENDING", "CONFIRMED", "CANCELLED"
    status = Column(String(20), nullable=False, default="CONFIRMED")
    cancellation_reason = Column(String, nullable=True)

    # Relationship linking to the parent doctor entity
    doctor = relationship("Doctor", back_populates="appointments")

    # THE CONCURRENCY BULLETPROOFING:
    # database-level composite unique index guarantees that two concurrent threads 
    # cannot successfully commit an identical active slot for a single doctor.
    # The second thread attempting a check-then-act collision will get hard-rejected by Postgres.
    
    __table_args__ = (
        Index(
            "idx_unique_active_doctor_slot",
            "doctor_id",
            "slot_time",
            unique=True,
            postgresql_where=text("status != 'CANCELLED'"),
            sqlite_where=text("status != 'CANCELLED'"),
        ),
    )
