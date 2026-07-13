FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed for PostgreSQL adapter compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects a dynamic $PORT variable at runtime; we bind to it
CMD ["sh", "-x", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]