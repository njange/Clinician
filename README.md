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

##  API Validation & Live Testing (Postman Verification)

The transactional workflows and scheduling constraints have been rigorously validated against the live Cloud Run production gateway using Postman. To adhere to data privacy expectations (such as Kenya ODPC guidelines), all response bodies strictly exclude sensitive Personally Identifiable Information (PII) like patient phone numbers or provider emails.

### 1. Dynamic Availability Discovery (`GET /doctors/{id}/availability`)
*   **Validation Rule:** Calculates unbooked 30-minute intervals dynamically by subtracting active reservations from the doctor's configured working hours.
*   **Behavior Check:** Slots already claimed vanish from this payload in real time.

![Postman - Doctor Availability](assets/availabile_slots.png)

### 2. Secure Appointment Booking (`POST /appointments`)
*   **Validation Rule:** Enforces that a slot falls inside working hours, is perfectly aligned to a 30-minute block, is not already taken, and respects the **1-hour future cutoff bonus constraint** to prevent short-notice scheduling.
*   **Behavior Check:** Yields an HTTP `201 Created` status with a sanitized response tracking only the scheduling tokens.

![Postman - Book Appointment](assets/appointments.png)

### 3. Immediate Slot Reclaim via Cancellation (`PATCH /appointments/{id}/cancel`)
*   **Validation Rule:** Requires a structured `cancellation_reason`. It flags the record state as `CANCELLED`, rendering the 30-minute block instantly bookable by other patients. 
*   **Idempotency Check:** Attempting to cancel an already cancelled appointment terminates early and returns a meaningful error code (`HTTP 400 Bad Request`).

![Postman - Cancel Appointment](assets/cancel_appointment.png)

### 4. Atomic Rescheduling Transaction (`PATCH /appointments/{id}/reschedule`)
*   **Validation Rule:** Executes within an isolated database transaction block (`SELECT ... FOR UPDATE`). It verifies the new target slot using the identical criteria as a fresh booking, updates the old slot back to a bookable pool, and builds the new reservation atomically.
*   **Safety Check:** If the original appointment was previously cancelled, the engine denies the patch request immediately.

![Postman - Reschedule Appointment](assets/reschedule_appointments.png)

### 5. Patient-Centric Timeline Agenda (`GET /patients/{id}/appointments/upcoming`)
*   **Validation Rule:** Pulls the specific patient portfolio, applies a chronological filter matching the current UTC timeline (`slot_time >= NOW()`), and sorts the outcome strictly by ascending date order.

![Postman - Patient Upcoming Appointments](assets/upcoming_appointments.png)

---

##  Error Validation & Structured Status Handling

The application maps engine anomalies to contextual HTTP status layers with clear, predictable error schemas:

| Threat Scenario | HTTP Status | Expected API Error Body Details |
| :--- | :--- | :--- |
| **Double Booking Race Condition** | `409 Conflict` | `"Value error, Appointment slot is already reserved."` *(Enforced by PostgreSQL unique index)* |
| **Short-Notice Scheduling** | `422 Unprocessable` | `"Value error, Appointment slot must be scheduled at least 1 hour in advance."` |
| **Past Datetime Payload** | `422 Unprocessable` | `"Value error, Appointment slot cannot be scheduled in the past."` |
| **Out of Bound Hours** | `400 Bad Request` | `"Requested slot time falls outside of the doctor's configured working hours."` |
| **Mutating a Cancelled Record** | `400 Bad Request` | `"Cannot reschedule/cancel an appointment that is already CANCELLED."` |

# Clinic Appointment Booking API

An optimized, high-concurrency healthcare slot scheduler built with FastAPI, SQLAlchemy, and PostgreSQL. The application features an automated, zero-downtime CI/CD engine deployed to Google Cloud Run in the Frankfurt (`europe-west3`) region.

## 🚀 Live Application URL
*   **Production API Gateway:** `https://clinic-backend-421781141134.europe-west3.run.app/`  *(Replace with your live URL)*
*   **Interactive API Docs (Swagger):** `https://clinic-backend-421781141134.europe-west3.run.app//docs`

---

## 🛠️ Architecture & Deployment Choices

### 🌍 Regional Infrastructure (Frankfurt)
The application architecture is explicitly constrained to the **Frankfurt (`europe-west3`)** geographical zone. 
*   **Data Sovereignty & Compliance:** Processing healthcare booking markers locally keeps execution within strict regional parameters.
*   **Sub-Millisecond Performance:** Co-locating the stateless Cloud Run container and the managed Cloud SQL PostgreSQL instances within the same zone allows communication over local Unix sockets, bypassing external internet routing latency.

### 🧪 Robust Test Isolation & Lifespan Architecture
*   **Global State Separation:** Database initializations (`create_all`) are shifted safely out of the global module import loop and into an isolated FastAPI `lifespan` hook. This ensures that unit tests can safely substitute connection engines without triggering connection attempts to production.
*   **Mocking Time-Dependent Rules:** To ensure assertions around scheduling blocks remain stable across time zones and variable network speeds, dynamic runtime checks are isolated using deterministic time offsets, eliminating CI/CD pipeline flakiness.

---

## 🤖 CI/CD Pipeline Workflow Runs

Our automated delivery engine runs through GitHub Actions on every change committed to the `main` branch.

### Pipeline Stages
1. **Test Suite:** Initializes a clean Python runtime environment, installs dependency schemas, and executes the full validation testing matrix using `pytest`.
2. **Authentication:** Authenticates securely against Google Cloud Platform using an IAM service account identity wrapper.
3. **Containerization:** Compiles the application layer into a lean Docker image and publishes it to the regional **GCP Artifact Registry**.
4. **Continuous Deployment:** Smoothly hands off the freshly published image to **Cloud Run**, establishing automated database socket proxies and secret environment variables dynamically.

### How to Run Tests Locally
```bash
# Install testing dependencies
pip install -r requirements.txt pytest httpx

# Execute test matrix
python -m pytest

## Technology

- FastAPI
- Pydantic
- SQLAlchemy
- PostgreSQL with `psycopg2`
- Pytest

## Testing Focus

Key tests should cover slot-grid generation, working-hour boundaries, one-hour booking cut-off, cancellation and rescheduling rollback, public-schema PII exclusion, and concurrent attempts to book the same doctor and slot.
