# ==========================================
# Stage 1: Build dependencies
# ==========================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build dependencies if needed (e.g. for PyMuPDF or general C builds)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install them user-locally
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ==========================================
# Stage 2: Production runtime image
# ==========================================
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH

# Copy installed Python packages from the builder stage
COPY --from=builder /root/.local /root/.local

# Copy the backend source files
COPY backend/ /app/

# Expose default FastAPI port
EXPOSE 8000

# Run FastAPI backend server using uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
