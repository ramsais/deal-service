FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies from requirements.txt (includes boto3/botocore)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY app/ ./app/

# Non-root user for security (ECS best practice)
RUN useradd -m appuser
USER appuser

EXPOSE 9000

# --timeout-graceful-shutdown ensures in-flight requests complete before ECS
# drains the task during deployments or scale-in events.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000", "--timeout-graceful-shutdown", "30"]
