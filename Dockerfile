# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# ── Stage 2: Runtime (lean, secure) ──────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

# Security: run as non-root user (principle of least privilege)
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy only installed packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy source code
COPY app/  ./app/

# Optional: copy pre-built Next.js static export
COPY frontend/out ./frontend/out 2>/dev/null || true

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
