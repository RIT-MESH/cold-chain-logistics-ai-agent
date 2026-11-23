# Production NeMo Guardrails service container (P8-E4 Stage 5)
# Multi-stage build: install deps, then copy app to a slim runtime

FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
LABEL description="Cold-Chain NeMo Guardrails security service"

# Non-root user
RUN groupadd -r nemo && useradd -r -g nemo nemo

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

WORKDIR /app
COPY production_service.py .
COPY config/ ./config/

# Switch to non-root
USER nemo

# Explicit port
EXPOSE 8080

# Health check (uses wget since curl may not be in slim)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

# Production startup
CMD ["uvicorn", "production_service:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
