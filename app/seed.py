from datetime import time
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Doctor

def seed_clinic_doctors(db: Session):
    """
    Seeds exactly 5 doctors (IDs 0 to 4) with varying shifts into the database
    if they do not already exist.
    """
    # Define 5 distinct work schedules spanning early, standard, and late shifts
    doctor_schedules = [
        {"id": 0, "full_name": "Dr. Amina Omondi", "email": "amina@clinic.co.ke", "phone": "+254700000000", "start": time(7, 0), "end": time(15, 0)},     # Early Shift
        {"id": 1, "full_name": "Dr. John Mwangi", "email": "john@clinic.co.ke", "phone": "+254711111111", "start": time(8, 0), "end": time(16, 0)},     # Standard Shift A
        {"id": 2, "full_name": "Dr. Silas Kiprop", "email": "silas@clinic.co.ke", "phone": "+254722222222", "start": time(9, 0), "end": time(17, 0)},   # Standard Shift B
        {"id": 3, "full_name": "Dr. Grace Mutua", "email": "grace@clinic.co.ke", "phone": "+254733333333", "start": time(10, 0), "end": time(18, 0)},   # Mid-Day Shift
        {"id": 4, "full_name": "Dr. David Patel", "email": "david@clinic.co.ke", "phone": "+254744444444", "start": time(12, 0), "end": time(20, 0)},   # Late/Evening Shift
    ]

    for doc in doctor_schedules:
        # Check if the doctor already exists to prevent duplication
        exists = db.query(Doctor).filter(Doctor.id == doc["id"]).first()
        if not exists:
            new_doctor = Doctor(
                id=doc["id"],
                full_name=doc["full_name"],
                email=doc["email"],
                personal_phone=doc["phone"],
                work_start=doc["start"],
                work_end=doc["end"]
            )
            db.add(new_doctor)
    
    db.commit()

if __name__ == "__main__":
    db = SessionLocal()
    try:
        print("Seeding clinic doctors into the database...")
        seed_clinic_doctors(db)
        print("Seeding completed successfully!")
    finally:
        db.close()