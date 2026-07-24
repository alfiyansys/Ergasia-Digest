FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 0.0.0.0 is required here — 127.0.0.1 inside the container isn't reachable
# via Docker's port mapping. The "not exposed externally" posture is instead
# enforced by only publishing to the host's loopback (see docker-compose.yml
# and PLAN.md §7).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
