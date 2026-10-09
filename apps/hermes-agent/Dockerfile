# Upstream main revision: e8c97320ac8691d4de92af49f98459f9ef9ddb08
ARG HERMES_BASE_IMAGE=docker.io/nousresearch/hermes-agent:main@sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde
FROM ${HERMES_BASE_IMAGE}

USER root

# Keep the Hermes venv first; upstream provides Node under /usr/local/bin.
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
    curl -fsSL --retry 3 --retry-delay 2 --max-time 120 "https://github.com/simpals/onest/releases/download/1.000/onest-fonts.zip" -o /tmp/onest-fonts.zip; \
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

# Expose upstream's pinned uv binary without installing a second toolchain.
RUN python3 -c 'from pathlib import Path; from pm.install import uv_launcher; binary = uv_launcher("uv"); assert binary is not None; Path("/usr/local/bin/uv").symlink_to(binary)'

# Only add packages not already bundled by upstream `.[all]`.
# Pin the versions validated in the existing Hermes runtime.
RUN uv pip install --no-cache-dir --python /opt/hermes/.venv/bin/python \
    "python-docx==1.2.0" \
    "pymupdf==1.28.2" \
    "pandas==3.0.5" \
    "matplotlib==3.11.2" \
    "yt-dlp[default]==2026.8.19" \
    "curl-cffi==0.16.3" \
    "PySocks==1.7.1" \
    "playwright==1.62.0" \
    "obsidiantools==0.11.0"

# Native plugin test tooling from this base's pyproject.toml and uv.lock.
# The runtime already supplies PyYAML, packaging and pygments. Do not sync the
# entire dev group or replace runtime dependencies to make pytest available.
RUN uv pip install --no-cache-dir --no-deps --python /opt/hermes/.venv/bin/python \
    "pytest==9.1.1" \
    "iniconfig==2.3.0" \
    "pluggy==1.6.0" \
    && /opt/hermes/.venv/bin/python -m pytest --version \
    && /opt/hermes/.venv/bin/python -c 'import sys, yaml; assert sys.version_info[:2] == (3, 14); assert sys.prefix == "/opt/hermes/.venv"; assert yaml.__version__ == "6.0.3"' \
    && uv pip check --python /opt/hermes/.venv/bin/python

# Proxy bootstrap Python (audit + Brave→SearXNG interception)
# Loaded via PYTHONPATH=/opt/hermes/bootstrap → sitecustomize.py
COPY --chown=0:0 proxy-bootstrap.py /opt/hermes/bootstrap/sitecustomize.py

# CDP patch: rewrites browserless 0.0.0.0 WebSocket URLs → Docker hostname
# Loaded via NODE_OPTIONS=--require=/opt/hermes/bootstrap/cdp-patch.js
COPY --chown=0:0 cdp-patch.js /opt/hermes/bootstrap/cdp-patch.js
COPY --chown=0:0 browserless-cdp-url.js /opt/hermes/bootstrap/browserless-cdp-url.js
COPY --chown=0:0 browserless-profile-live.js browserless-cdp-broker.py pocket-tts-client.py wrangler-auth.py /opt/hermes/bootstrap/

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
    curl -fsSL --retry 3 --retry-delay 2 --max-time 120 "https://github.com/openclaw/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz" \
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
    curl -fsSL --retry 3 --retry-delay 2 --max-time 120 "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}.zip" \
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
COPY --chown=0:0 entrypoint-wrapper.sh /opt/hermes/entrypoint-wrapper.sh
COPY --chown=0:0 spartan_browser_policy.py /opt/hermes/bootstrap/spartan_browser_policy.py
COPY --chown=0:0 spartan_browser_policy.py /opt/hermes/spartan_browser_policy.py
COPY --chown=0:0 --chmod=0644 spartan_camofox_vault.py /opt/hermes/spartan_camofox_vault.py
COPY --chown=0:0 spartan_browser_variant.py /opt/hermes/patches/spartan_browser_variant.py
COPY --chown=0:0 spartan-browser-variant-init.sh /opt/hermes/patches/spartan-browser-variant-init.sh
COPY --chown=0:0 spartan-browser-policy-init.sh /etc/cont-init.d/0195-spartan-browser-policy
ENV SPARTAN_BROWSER_POLICY_ENABLED=1 \
    HERMES_MANAGED_DIR=/run/spartan-hermes-managed \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2

# Build-time patches for upstream Hermes files (survive base image updates)
COPY --chown=0:0 patches/ /opt/hermes/patches/
COPY --chown=0:0 audit-install-tree.py /opt/hermes/patches/audit-install-tree.py
# Apply patches and verify permissions atomically; avoid one snapshot per patch.
RUN python3 /opt/hermes/patches/patch_stage2_runtime_owner.py \
    && python3 /opt/hermes/patches/patch_s6_service_permissions.py \
    && python3 /opt/hermes/patches/patch_runtime_identity_drop.py \
    && python3 /opt/hermes/patches/patch_stage2_cdp_env.py \
    && python3 /opt/hermes/patches/patch_gateway_service_cdp_env.py \
    && python3 /opt/hermes/patches/patch_camofox_tab_isolation.py \
    && python3 /opt/hermes/patches/patch_camofox_managed_policy.py \
    && python3 /opt/hermes/patches/patch_camofox_vault.py \
    && python3 /opt/hermes/patches/patch_prompt_load_callback.py \
    && python3 /opt/hermes/patches/patch_checkpoint_rollback_scan.py \
    && python3 /opt/hermes/patches/patch_mcp_proxy_env.py \
    && python3 /opt/hermes/patches/patch_google_meet_cdp.py \
    && python3 /opt/hermes/patches/patch_google_meet_transcript.py \
    && python3 /opt/hermes/patches/patch_clawroute_prompt_cache_key.py \
    && python3 /opt/hermes/patches/patch_spartan_fast_mode.py \
    && python3 /opt/hermes/patches/patch_route_env_placeholders.py \
    && python3 /opt/hermes/patches/patch_codex_429_fail_fast.py \
    && python3 /opt/hermes/patches/patch_workstation_approval_scope.py \
    && python3 /opt/hermes/patches/patch_state_fts_empty_migration.py \
    && python3 /opt/hermes/patches/patch_async_completion_scope.py \
    && python3 /opt/hermes/patches/patch_lsp_sdk_repair.py \
    && python3 /opt/hermes/patches/patch_cron_thread_diagnostic.py \
    && python3 /opt/hermes/patches/patch_stage2_image_dependencies.py \
    && python3 /opt/hermes/patches/freeze_image_features.py \
    && if [ -f /etc/spartan/hermes-browser-mode ]; then \
        install -m 0644 /opt/hermes/patches/spartan_browser_variant.py /opt/hermes/spartan_browser_variant.py \
        && install -m 0644 /opt/hermes/patches/spartan_browser_variant.py /opt/hermes/bootstrap/spartan_browser_variant.py \
        && install -m 0755 /opt/hermes/patches/spartan-browser-variant-init.sh /etc/cont-init.d/0196-spartan-browser-variant \
        && chmod 0644 /opt/hermes/patch_camofox_only_variant.py \
        && python3 /opt/hermes/patches/patch_camofox_only_variant.py /opt/hermes \
            --helper /opt/hermes/spartan_browser_variant.py --meet-only; \
    fi \
    && chmod 0755 /opt/hermes/bootstrap /opt/hermes/entrypoint-wrapper.sh \
    && chmod 0755 /etc/cont-init.d/0195-spartan-browser-policy \
    && chmod 0644 /opt/hermes/spartan_browser_policy.py \
    && find /opt/hermes/bootstrap -maxdepth 1 -type f -exec chmod 0644 {} + \
    && chmod -R a+rX,go-w /opt/hermes/patches \
    && if [ -f /opt/hermes/.venv/.lock ]; then chmod 0644 /opt/hermes/.venv/.lock; fi \
    && python3 /opt/hermes/patches/audit-install-tree.py /opt/hermes \
    && test "$(stat -c '%a' /opt/hermes/bootstrap)" = "755" \
    && test "$(stat -c '%u:%g' /opt/hermes/tools/browser_camofox.py)" = "0:0" \
    && test "$(stat -c '%u:%g' /opt/hermes/tools/browser_tool.py)" = "0:0" \
    && setpriv --reuid=65534 --regid=65534 --clear-groups \
        python3 -c 'from pathlib import Path; [Path(path).read_bytes() for path in ("/opt/hermes/bootstrap/cdp-patch.js", "/opt/hermes/tools/browser_camofox.py", "/opt/hermes/tools/browser_tool.py")]'

# NOTE: No USER directive - the container starts as root so upstream /init can
# run cont-init hooks, drop Hermes workloads to the configured UID/GID, and
# supervise services.

ENTRYPOINT ["/init", "/opt/hermes/entrypoint-wrapper.sh"]
