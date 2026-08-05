# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install MeloTTS; all required deps (pypinyin, jieba, mecab, etc.) are in requirements.txt
RUN pip install --no-cache-dir cn2an==0.5.22 || pip install --no-cache-dir cn2an
RUN pip install --no-cache-dir git+https://github.com/myshell-ai/MeloTTS.git

# ─── Stage 2: Final ───────────────────────────────────────────────────────────
FROM python:3.11-slim AS final

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy project files
COPY . .

# ─── Runtime environment ──────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Point ALL model caches to /tmp — writable by any user, survives the build
# Whisper (CTranslate2 / faster-whisper)
ENV HF_HOME=/tmp/hf_cache
ENV HUGGINGFACE_HUB_CACHE=/tmp/hf_cache
ENV TRANSFORMERS_CACHE=/tmp/hf_cache
# MeloTTS downloads to XDG cache
ENV XDG_CACHE_HOME=/tmp/xdg_cache
# Torch model dir
ENV TORCH_HOME=/tmp/torch_cache

EXPOSE 8000

# Non-root user for security
RUN useradd -u 1001 -d /app -s /bin/sh appuser && chown -R appuser:appuser /app
USER appuser

# Uvicorn with single worker (model singletons only work with 1 worker)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
