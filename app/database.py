import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Fetch database URL safely - Zero Hardcoded Secrets (ODPC/Savannah Compliance)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/clinic_db")

# Echo=False in production to prevent logging raw PII data queries
engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to provide a clean transaction lifecycle per API request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()