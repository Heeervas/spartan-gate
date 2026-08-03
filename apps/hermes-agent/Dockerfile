FROM docker.io/nousresearch/hermes-agent:main@sha256:ed51b14789be47f727dfd6e3fd1dd796c2c037891a85b24a7c550695a4e92869

USER root

# Keep the Hermes venv first; upstream provides Node 22 under /usr/local/bin.
ENV PATH="/opt/hermes/.venv/bin:$PATH"

# Ensure venv PATH survives login shells (bash -l, su -, SSH, sh)
# /etc/profile.d/ is sourced by both bash and sh login shells
RUN echo 'export PATH="/opt/hermes/.venv/bin:$PATH"' > /etc/profile.d/hermes-venv.sh \
    && node -v \
    && npm -v

# CLI-Toolbox: Network, System, Dev, Data Processing, Media
# (matches Hermes's toolbox minus packages the base image already includes)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    # --- Network ---
    wget \
    dnsutils \
    netcat-openbsd \
    traceroute \
    nmap \
    whois \
    rsync \
    # --- System (procps already in base image since v0.8.0) ---
    htop \
    less \
    file \
    tree \
    zip \
    unzip \
    # --- Editors ---
    nano \
    vim-tiny \
    # --- Dev (git already in base image since v0.9.0) ---
    gh \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    # --- Data Processing ---
    jq \
    bc \
    xmlstarlet \
    # --- Documents ---
    poppler-utils \
    pandoc \
    # --- Media ---
    imagemagick \
    # --- Archives ---
    p7zip-full \
    # --- TeX / Manim ---
    dvisvgm \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-science \
    # --- Diagnostics ---
    lsof \
    # --- TLS / Certs ---
    openssl \
    fontconfig \
    libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install the complete static Onest 1.000 release and make it available to all
# Fontconfig consumers (browser automation, image capture, and PDF tooling).
RUN set -eux; \
    curl -fsSL "https://github.com/simpals/onest/releases/download/1.000/onest-fonts.zip" -o /tmp/onest-fonts.zip; \
    echo "e44491d1f892716036307beddb4cad5adae52aac7fb70f8c274198c8aea3cb15  /tmp/onest-fonts.zip" | sha256sum -c -; \
    mkdir -p /tmp/onest /usr/local/share/fonts/onest; \
    unzip /tmp/onest-fonts.zip "onest-fonts/fonts/ttf/*.ttf" -d /tmp/onest; \
    for font in \
        Onest-Black.ttf \
        Onest-Bold.ttf \
        Onest-ExtraBold.ttf \
        Onest-Light.ttf \
        Onest-Medium.ttf \
        Onest-Regular.ttf \
        Onest-Thin.ttf; \
    do \
        install -m 0644 "/tmp/onest/onest-fonts/fonts/ttf/${font}" /usr/local/share/fonts/onest/; \
    done; \
    [ "$(find /usr/local/share/fonts/onest -type f -name '*.ttf' | wc -l)" -eq "7" ]; \
    fc-cache -fv; \
    fc-list | grep -i Onest; \
    rm -rf /tmp/onest /tmp/onest-fonts.zip

# Persist APT proxy so runtime builds inside the container use the outbound proxy
RUN echo 'Acquire::http::Proxy "http://proxy:8888";' > /etc/apt/apt.conf.d/99proxy \
    && echo 'Acquire::https::Proxy "http://proxy:8888";' >> /etc/apt/apt.conf.d/99proxy

# Only add packages not already bundled by upstream `.[all]`.
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
    python-docx \
    pymupdf \
    pandas \
    matplotlib \
    "yt-dlp[default]" \
    curl-cffi \
    PySocks \
    playwright \
    obsidiantools

# Proxy bootstrap Python (audit + Brave→SearXNG interception)
# Loaded via PYTHONPATH=/opt/hermes/bootstrap → sitecustomize.py
COPY --chown=0:0 --chmod=0644 proxy-bootstrap.py /opt/hermes/bootstrap/sitecustomize.py

# CDP patch: rewrites browserless 0.0.0.0 WebSocket URLs → Docker hostname
# Loaded via NODE_OPTIONS=--require=/opt/hermes/bootstrap/cdp-patch.js
COPY --chown=0:0 --chmod=0644 cdp-patch.js /opt/hermes/bootstrap/cdp-patch.js
COPY --chown=0:0 --chmod=0644 browserless-cdp-url.js /opt/hermes/bootstrap/browserless-cdp-url.js
COPY --chown=0:0 --chmod=0644 browserless-profile-live.js /opt/hermes/bootstrap/browserless-profile-live.js
COPY --chown=0:0 --chmod=0644 browserless-cdp-broker.py /opt/hermes/bootstrap/browserless-cdp-broker.py
COPY --chown=0:0 --chmod=0644 pocket-tts-client.py /opt/hermes/bootstrap/pocket-tts-client.py

# Install gogcli (Google CLI — Gmail, Calendar, Drive, Docs, Sheets, etc.)
# https://github.com/openclaw/gogcli
RUN set -eux; \
    GOGCLI_VERSION="0.30.0"; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) GOGCLI_SHA256="f9f4f407bb762959085b42007c4da538b3dafc5c76b53f3a30dc7ff7c6237fc1" ;; \
        arm64) GOGCLI_SHA256="3ba0ce1c268bc95cf84c60c622e1e4cbf3bc257ecd6e447bd1cfcdd3aa09430a" ;; \
        *) echo "Unsupported arch: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    GOGCLI_ARCHIVE="/tmp/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz"; \
    curl -fsSL "https://github.com/openclaw/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz" \
        -o "${GOGCLI_ARCHIVE}"; \
    echo "${GOGCLI_SHA256}  ${GOGCLI_ARCHIVE}" | sha256sum -c -; \
    tar xzf "${GOGCLI_ARCHIVE}" -C /usr/local/bin gog; \
    rm -f "${GOGCLI_ARCHIVE}"; \
    chmod +x /usr/local/bin/gog

# Install Deno (JS runtime required by yt-dlp EJS challenge solver)
RUN set -eux; \
    DENO_VERSION="2.2.3"; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) DENO_ARCH="x86_64-unknown-linux-gnu"; DENO_SHA256="9b2928c032fd9fa058245b0105ca6382007d8f047ae8c1f4b40ea22106f591ed" ;; \
        arm64) DENO_ARCH="aarch64-unknown-linux-gnu"; DENO_SHA256="a63a5a70d44d7a20d1f6f2894dcd14204f6bb350635d038f7c31989730b43b3a" ;; \
        *) echo "Unsupported arch: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    DENO_ARCHIVE="/tmp/deno-${DENO_ARCH}.zip"; \
    curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}.zip" \
        -o "${DENO_ARCHIVE}"; \
    echo "${DENO_SHA256}  ${DENO_ARCHIVE}" | sha256sum -c -; \
    unzip "${DENO_ARCHIVE}" -d /usr/local/bin; \
    rm -f "${DENO_ARCHIVE}"; \
    chmod +x /usr/local/bin/deno

# Spartan Gate keeps installed Hermes code root-owned, while local deployments
# may drop Hermes workloads to the host data owner's numeric UID/GID.

# Persist pip/uv proxy config (created as root; gosu drops to hermes at runtime)
RUN mkdir -p /opt/data/.config/pip \
    && printf '[global]\nproxy = http://proxy:8888\n' > /opt/data/.config/pip/pip.conf

# Main-program wrapper: runs Spartan-specific boot hooks after upstream s6
# cont-init setup, then delegates command routing and privilege drop to upstream.
COPY --chown=0:0 --chmod=0644 wrangler-auth.py /opt/hermes/bootstrap/wrangler-auth.py
COPY --chown=0:0 --chmod=0755 entrypoint-wrapper.sh /opt/hermes/entrypoint-wrapper.sh

# Build-time patches for upstream Hermes files (survive base image updates)
COPY --chown=0:0 --chmod=a+rX,go-w patches/ /opt/hermes/patches/
RUN python3 /opt/hermes/patches/patch_stage2_runtime_owner.py
RUN python3 /opt/hermes/patches/patch_s6_service_permissions.py
RUN python3 /opt/hermes/patches/patch_runtime_identity_drop.py
RUN python3 /opt/hermes/patches/patch_stage2_cdp_env.py
RUN python3 /opt/hermes/patches/patch_gateway_service_cdp_env.py
RUN python3 /opt/hermes/patches/patch_camofox_tab_isolation.py
RUN python3 /opt/hermes/patches/patch_prompt_load_callback.py
RUN python3 /opt/hermes/patches/patch_checkpoint_rollback_scan.py
RUN python3 /opt/hermes/patches/patch_mcp_proxy_env.py
RUN python3 /opt/hermes/patches/patch_google_meet_cdp.py
RUN python3 /opt/hermes/patches/patch_google_meet_transcript.py
RUN python3 /opt/hermes/patches/patch_clawroute_prompt_cache_key.py
RUN python3 /opt/hermes/patches/patch_route_env_placeholders.py
RUN python3 /opt/hermes/patches/patch_codex_429_fail_fast.py
RUN python3 /opt/hermes/patches/patch_workstation_approval_scope.py

# Fail closed on upstream or patch ownership drift without rewriting the full
# venv/node_modules tree into a slow copy-on-write chown layer.
COPY --chown=0:0 --chmod=0644 audit-install-tree.py /opt/hermes/patches/audit-install-tree.py
RUN chmod 0644 /opt/hermes/.venv/.lock \
    && python3 /opt/hermes/patches/audit-install-tree.py /opt/hermes
RUN test "$(stat -c '%u:%g' /opt/hermes/tools/browser_camofox.py)" = "0:0" \
    && test "$(stat -c '%u:%g' /opt/hermes/tools/browser_tool.py)" = "0:0" \
    && setpriv --reuid=65534 --regid=65534 --clear-groups \
        python3 -c 'from pathlib import Path; [Path(path).read_bytes() for path in ("/opt/hermes/tools/browser_camofox.py", "/opt/hermes/tools/browser_tool.py")]'

# NOTE: No USER directive - the container starts as root so upstream /init can
# run cont-init hooks, drop Hermes workloads to the configured UID/GID, and
# supervise services.

ENTRYPOINT ["/init", "/opt/hermes/entrypoint-wrapper.sh"]
