# CLOCKIN — production container
# Multi-stage build keeps the final image small.

# ---------- Stage 1: build wheels ----------
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile bcrypt / Pillow wheels if no prebuilt binary exists
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install into a user-local prefix so the runtime stage can copy a single folder
RUN pip install --user --no-cache-dir -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim

# Run as a non-root user for safety
RUN useradd --create-home --shell /bin/bash clockin

WORKDIR /app

# Copy the installed Python packages from the build stage
COPY --from=builder /root/.local /home/clockin/.local

# Copy the application code (respecting .dockerignore)
COPY --chown=clockin:clockin . .

# Make sure /app/data exists and is owned by the runtime user
# (the volume mount will shadow this at runtime, but we set it up for first run)
RUN mkdir -p /app/data && chown -R clockin:clockin /app/data

USER clockin

# Put the user-local bin (where gunicorn lives) on PATH
ENV PATH=/home/clockin/.local/bin:$PATH

# Default port — overridable via environment if needed
ENV CLOCKIN_PORT=5000
EXPOSE 5000

# Docker can restart the container if this fails 3 times in a row.
# Hits the kiosk page (the public route) so it works even before setup.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/', timeout=3).read()" || exit 1

# Gunicorn = production-grade WSGI server (replaces Flask's dev server).
# 2 workers is enough for a single classroom; bump later if needed.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "app:app"]
