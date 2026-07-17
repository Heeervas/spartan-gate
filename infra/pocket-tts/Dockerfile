FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.10.0+cpu" \
    && python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /app/requirements.txt \
    && python -c "import importlib.metadata, torch; assert torch.__version__.startswith('2.10.0'); assert importlib.metadata.version('pocket-tts') == '2.1.0'"

COPY runtime.py server.py prefetch.py /app/

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin pockettts

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "/app/server.py"]
