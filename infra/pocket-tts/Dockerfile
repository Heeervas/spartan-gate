FROM python:3.13-slim-bookworm@sha256:dd86541a59b252667f4c12f8b2ee17216de37dd65ac773bf097bef996fa78860

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.10.0+cpu" \
    && python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /app/requirements.txt \
    && python -m pip check

COPY audio.py runtime.py residency.py server.py prefetch.py benchmark.py /app/

# Bind the installed, hash-checked wheel to the cache manifest's provenance.
RUN python -c "import importlib.metadata as m, json, torch; from runtime import POCKET_TTS_VERSION, POCKET_TTS_WHEEL_URL, POCKET_TTS_WHEEL_SHA256; d = m.distribution('pocket-tts'); p = json.loads(d.read_text('direct_url.json')); assert torch.__version__ == '2.10.0+cpu'; assert d.version == POCKET_TTS_VERSION; assert p['url'] == POCKET_TTS_WHEEL_URL; assert p['archive_info']['hashes']['sha256'] == POCKET_TTS_WHEEL_SHA256"

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin pockettts

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "/app/server.py"]
