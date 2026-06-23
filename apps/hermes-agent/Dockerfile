FROM node:22-bookworm-slim AS node22

FROM docker.io/nousresearch/hermes-agent:main@sha256:2fd49b834b886db260d829884908932c88eb9f0b2782efd44a727cda5b02d7fe

USER root

# Keep an explicit Node 22 overlay until the upgraded image has proven the same
# node/npm/npx/corepack availability on Spartan Gate's expected paths.
COPY --from=node22 /usr/local/bin/node /usr/local/bin/node
COPY --from=node22 /usr/local/include/node /usr/local/include/node
COPY --from=node22 /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

# Keep the Hermes venv first; Node 22 resolves from /usr/local/bin via the
# existing system PATH ahead of Debian's /usr/bin/node.
ENV PATH="/opt/hermes/.venv/bin:$PATH"

# Ensure venv PATH survives login shells (bash -l, su -, SSH, sh)
# /etc/profile.d/ is sourced by both bash and sh login shells
RUN echo 'export PATH="/opt/hermes/.venv/bin:$PATH"' > /etc/profile.d/hermes-venv.sh \
    && node -v \
    && npm -v

# CLI-Toolbox: Network, System, Dev, Data Processing, Media
# (matches Hermes's toolbox minus packages the base image already includes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # --- Network ---
    curl \
    wget \
    iputils-ping \
    dnsutils \
    netcat-openbsd \
    traceroute \
    nmap \
    whois \
    openssh-client \
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
    make \
    g++ \
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
    ffmpeg \
    # --- Archives ---
    p7zip-full \
    xz-utils \
    # --- TeX / Manim ---
    dvisvgm \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-science \
    # --- Diagnostics ---
    lsof \
    # --- TLS / Certs ---
    ca-certificates \
    openssl \
    libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

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
COPY proxy-bootstrap.py /opt/hermes/bootstrap/sitecustomize.py

# CDP patch: rewrites browserless 0.0.0.0 WebSocket URLs → Docker hostname
# Loaded via NODE_OPTIONS=--require=/opt/hermes/bootstrap/cdp-patch.js
COPY cdp-patch.js /opt/hermes/bootstrap/cdp-patch.js
COPY browserless-cdp-url.js /opt/hermes/bootstrap/browserless-cdp-url.js
COPY browserless-profile-live.js /opt/hermes/bootstrap/browserless-profile-live.js
COPY browserless-cdp-broker.py /opt/hermes/bootstrap/browserless-cdp-broker.py
COPY runtime-docs/ /opt/hermes/runtime-docs/

# Install gogcli (Google CLI — Gmail, Calendar, Drive, Docs, Sheets, etc.)
# https://github.com/steipete/gogcli
RUN set -eux; \
    GOGCLI_VERSION="0.12.0"; \
    ARCH="$(dpkg --print-architecture)"; \
    curl -fsSL "https://github.com/steipete/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz" \
        | tar xz -C /usr/local/bin gog; \
    chmod +x /usr/local/bin/gog

# Install Deno (JS runtime required by yt-dlp EJS challenge solver)
RUN set -eux; \
    DENO_VERSION="2.2.3"; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) DENO_ARCH="x86_64-unknown-linux-gnu" ;; \
        arm64) DENO_ARCH="aarch64-unknown-linux-gnu" ;; \
        *) echo "Unsupported arch: ${ARCH}" && exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}.zip" \
        -o /tmp/deno.zip; \
    unzip /tmp/deno.zip -d /usr/local/bin; \
    rm /tmp/deno.zip; \
    chmod +x /usr/local/bin/deno

# Spartan Gate keeps installed Hermes code root-owned, while local deployments
# may drop Hermes workloads to the host data owner's numeric UID/GID.

# Persist pip/uv proxy config (created as root; gosu drops to hermes at runtime)
RUN mkdir -p /opt/data/.config/pip \
    && printf '[global]\nproxy = http://proxy:8888\n' > /opt/data/.config/pip/pip.conf

# Main-program wrapper: runs Spartan-specific boot hooks after upstream s6
# cont-init setup, then delegates command routing and privilege drop to upstream.
COPY entrypoint-wrapper.sh /opt/hermes/entrypoint-wrapper.sh
RUN chmod +x /opt/hermes/entrypoint-wrapper.sh

# Build-time patches for upstream Hermes files (survive base image updates)
COPY patches/ /opt/hermes/patches/
RUN python3 /opt/hermes/patches/patch_stage2_install_chown_guard.py
RUN python3 /opt/hermes/patches/patch_s6_service_permissions.py
RUN python3 /opt/hermes/patches/patch_runtime_identity_drop.py
RUN python3 /opt/hermes/patches/patch_stage2_cdp_env.py
RUN python3 /opt/hermes/patches/patch_gateway_service_cdp_env.py
RUN python3 /opt/hermes/patches/patch_prompt_load_callback.py
RUN python3 /opt/hermes/patches/patch_checkpoint_rollback_scan.py
RUN python3 /opt/hermes/patches/patch_mcp_proxy_env.py
RUN python3 /opt/hermes/patches/patch_gateway_retryable_startup.py
RUN python3 /opt/hermes/patches/patch_google_meet_cdp.py
RUN python3 /opt/hermes/patches/patch_google_meet_transcript.py
RUN python3 /opt/hermes/patches/patch_camofox_access_key.py
RUN python3 /opt/hermes/patches/patch_clawroute_prompt_cache_key.py

# Keep installed code owned by root so the hermes workload cannot mutate future
# Python imports, gateway source, Node dependencies, or bundled patches.
RUN chown -R root:root /opt/hermes

# NOTE: No USER directive - the container starts as root so upstream /init can
# run cont-init hooks, drop Hermes workloads to the configured UID/GID, and
# supervise services.

ENTRYPOINT ["/init", "/opt/hermes/entrypoint-wrapper.sh"]
