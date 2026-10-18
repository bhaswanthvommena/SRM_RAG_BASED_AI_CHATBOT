# CPU image for Cloud Run (PORT, default 8080) and Hugging Face Spaces (7860).
FROM python:3.11-slim-bookworm

RUN useradd -m -u 1000 user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/user/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/user/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/torch/sentence_transformers

WORKDIR $HOME/app

# System deps for sentence-transformers / scientific stack
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install as root into user site, then drop privileges
RUN pip install --upgrade pip

# CPU-only torch first (smaller than default CUDA wheels)
RUN pip install --no-cache-dir --user \
    torch --index-url https://download.pytorch.org/whl/cpu

COPY --chown=user backend/requirements-space.txt $HOME/app/backend/requirements-space.txt
RUN pip install --no-cache-dir --user -r backend/requirements-space.txt

COPY --chown=user backend/ $HOME/app/backend/

USER user

ENV PYTHONPATH=$HOME/app/backend
# Cloud Run overrides PORT. Hugging Face Spaces do not, so keep 7860 as the default.
ENV PORT=7860

# Prefetch BGE so the first chat request is faster after boot
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
