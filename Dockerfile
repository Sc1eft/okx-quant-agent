# ──────────────────────────────────────────────────────
# OKX Quant Agent — Docker Image
# ──────────────────────────────────────────────────────
# Build:  docker build -t okx-quant-agent .
# Usage:
#   Agent:   docker run -v ./data:/app/data okx-quant-agent python main.py --mode paper
#   Web:     docker run -p 8501:8501 -v ./data:/app/data okx-quant-agent
#   Compose: docker compose up
# ──────────────────────────────────────────────────────

FROM python:3.11-slim AS base

WORKDIR /app

# ── System deps ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy project ──
COPY . .

# ── Runtime dirs ──
RUN mkdir -p /app/data /app/logs /app/data/reviews

# ── Expose Streamlit ──
EXPOSE 8501

# ── Health check ──
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# ── Default: Streamlit frontend ──
CMD ["streamlit", "run", "frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]

# ══════════════════════════════════════════════════════
# Multi-stage: dev (with test deps)
# ══════════════════════════════════════════════════════
FROM base AS dev

RUN pip install --no-cache-dir pytest>=7.4.0 ipython

CMD ["python", "main.py", "--mode", "demo"]
