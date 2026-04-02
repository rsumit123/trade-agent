FROM python:3.13-slim

# Install tini for proper zombie process reaping (agent subprocesses)
RUN apt-get update && apt-get install -y --no-install-recommends tini curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agent/ ./agent/
COPY dashboard/ ./dashboard/
COPY run.py .
COPY scripts/ ./scripts/

# Create directories for session data (will be overridden by volume mounts)
RUN mkdir -p sessions data learnings logs

EXPOSE 8000

# Health check — lightweight endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/sessions || exit 1

# Use tini as init to properly handle signals and reap zombie agent processes
ENTRYPOINT ["tini", "--"]

# Single uvicorn worker (required: in-memory state + subprocess PID tracking)
CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
