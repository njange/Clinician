# Clinic Appointment Booking System

A FastAPI and PostgreSQL service for discovering, booking, cancelling, and rescheduling doctor appointments. The system uses fixed 30-minute appointment slots and prioritizes data consistency, privacy, and protection against double-booking.

## Goals

- Show a doctor's available 30-minute slots for a selected day.
- Book, cancel, and reschedule appointments safely.
- Prevent bookings less than one hour before the appointment time.
- Make cancelled slots available immediately.
- Return a patient's upcoming appointments.
- Keep sensitive patient and provider information out of public API responses.

## Architecture

The application follows a simple monolithic layered design:

```text
Client
  | HTTPS
  v
FastAPI application
  |- API routers: request handling and Pydantic response schemas
  |- Services: validation and booking rules
  `- Data access: SQLAlchemy queries and transactions
  |
  v
PostgreSQL
```

PostgreSQL is the source of truth for appointment state. Appointment timestamps are stored as `TIMESTAMPTZ` and handled in UTC.

## Planned API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/doctors/{id}/availability?date=YYYY-MM-DD` | List unbooked 30-minute slots during the doctor's working hours. |
| `POST` | `/appointments` | Create an appointment for an available slot. |
| `PATCH` | `/appointments/{id}/cancel` | Cancel an appointment; a cancellation reason is required. |
| `PATCH` | `/appointments/{id}/reschedule` | Atomically move an appointment to a new eligible slot. |
| `GET` | `/patients/{id}/appointments/upcoming` | Retrieve a patient's upcoming appointments. |
| `GET` | `/health` | Report service and database health. |

## Core Rules

- Every appointment occupies one fixed 30-minute slot.
- A requested slot must fall within the doctor's configured working hours.
- A booking must be at least one hour in the future.
- Cancelled appointments cannot be cancelled again.
- Availability is calculated dynamically: working-hour slots minus active bookings.
- Existing appointments remain valid if a doctor's working hours later change; only future availability is affected.

## Preventing Double Bookings

Application checks improve the user experience, but the database provides the final concurrency guarantee. PostgreSQL enforces one active appointment per doctor and time slot with a partial unique index:

```sql
CREATE UNIQUE INDEX idx_unique_active_doctor_slot
ON appointments (doctor_id, slot_time)
WHERE status != 'CANCELLED';
```

If two requests try to reserve the same slot at nearly the same time, only one can succeed. The application should translate the resulting unique-constraint violation into a clear conflict response (for example, HTTP `409`).

Rescheduling is performed in a single database transaction:

1. Lock the existing appointment row with `SELECT ... FOR UPDATE`.
2. Validate the requested slot.
3. Mark the old appointment cancelled and create the replacement appointment.
4. Commit only if every operation succeeds; otherwise roll back and keep the original appointment.

## Data Model

### `doctors`

| Field | Notes |
| --- | --- |
| `id` | Primary key |
| `full_name` | Provider name |
| `email` | Private provider contact data |
| `personal_phone` | Private provider contact data |
| `work_start`, `work_end` | Daily appointment boundaries |

### `appointments`

| Field | Notes |
| --- | --- |
| `id` | Primary key |
| `doctor_id` | Foreign key to `doctors` |
| `patient_id` | Patient identifier |
| `slot_time` | UTC appointment start time (`TIMESTAMPTZ`) |
| `status` | Appointment state, including `CANCELLED` |
| `cancellation_reason` | Required when cancelling |

## Privacy and Security

This service is designed with Kenya ODPC data-protection expectations in mind:

- Use separate ORM models and public Pydantic response schemas.
- Never expose provider email addresses, phone numbers, patient identifiers, or other unnecessary PII in public payloads.
- Keep secrets in environment variables or a managed secret store; do not commit them.
- Use a least-privilege database account for the application.
- Use structured logs with request context, while excluding patient names and identifiers.
- Serve the API over HTTPS in deployed environments.

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL 14+

### Install dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Configure the database

Set a `DATABASE_URL` environment variable for a PostgreSQL database, for example:

```text
postgresql+psycopg2://<user>:<password>@localhost:5432/clinic
```

Create the database schema and the active-slot unique index before accepting booking traffic.

### Run the API

```bash
uvicorn app.main:app --reload
```

When the application is implemented, interactive documentation will be available at `http://127.0.0.1:8000/docs`.

## Technology

- FastAPI
- Pydantic
- SQLAlchemy
- PostgreSQL with `psycopg2`
- Pytest

## Testing Focus

Key tests should cover slot-grid generation, working-hour boundaries, one-hour booking cut-off, cancellation and rescheduling rollback, public-schema PII exclusion, and concurrent attempts to book the same doctor and slot.
