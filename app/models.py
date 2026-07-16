from sqlalchemy import Column, Integer, String, Time, DateTime, ForeignKey, Index, text, Enum, Boolean
from sqlalchemy.orm import relationship
from enum import Enum as pyEnum
from .database import Base
from enum import Enum as PyEnum

class UserRole(str, pyEnum):
    PATIENT = "patient"
    DOCTOR = "doctor"

class AppointmentStatus(str, PyEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    full_name = Column(String(100), nullable=False)

    email = Column(String(255), unique=True, index=True, nullable=False)

    password_hash = Column(String(255), nullable=False)

    role = Column(
        Enum(UserRole),
        nullable=False,
        default=UserRole.PATIENT,
    )

    is_active = Column(Boolean, default=True)
    
class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
    Integer,
    ForeignKey("users.id", ondelete="CASCADE"),
    unique=True,
    nullable=False,
    )

    user = relationship(
    "User",
    back_populates="doctor",
    )
    
    # infrastructure fields. 
    phone_number = Column(String(20), nullable=False)
    
    # Working hours bounds (Stored natively as Time fields without timezones)
    work_start = Column(Time, nullable=False)
    work_end = Column(Time, nullable=False)

    # Relationship linking back to booked/cancelled appointments
    appointments = relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )

    user = relationship(
    "User",
    back_populates="patient",
    )

    appointments = relationship(
    "Appointment",
    back_populates="patient",
    )

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    
    # Crucial: Enforced explicitly as DateTime with Timezone (TIMESTAMPTZ in Postgres)
    # preventing varying localized engine/server time offset bugs.
    slot_time = Column(DateTime(timezone=True), nullable=False)
    
    status = Column(
    Enum(AppointmentStatus, name="appointment_status"),
    nullable=False,
    default=AppointmentStatus.CONFIRMED,
    )

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

    patient = relationship(
    "Patient",
    back_populates="appointments",
    )

    created_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    nullable=False,
    )

    updated_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    onupdate=func.now(),
    nullable=False,
    )

