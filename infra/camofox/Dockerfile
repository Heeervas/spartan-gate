ARG CAMOFOX_BASE_IMAGE=ghcr.io/jo-inc/camofox-browser:1.15.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae
FROM ${CAMOFOX_BASE_IMAGE}

# Upstream installs the browser, font tools, VNC stack and verified yt-dlp.
# Assert that contract instead of upgrading/reinstalling those packages here.
RUN command -v curl && command -v unzip && command -v fc-cache \
    && command -v x11vnc && command -v websockify \
    && test -f /usr/share/novnc/vnc_lite.html

# Install the complete static Onest 1.000 release for Firefox and capture tools.
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

# Prefetch the exact AMO-signed uBlock Origin release into camoufox-js's cache.
# The runtime service has no direct egress, and the AMO API publishes this
# immutable file URL and SHA-256 for version 1.72.2.
RUN set -eux; \
    UBLOCK_ORIGIN_VERSION="1.72.2"; \
    UBLOCK_ORIGIN_SHA256="40c315b0da7871868155ecfae7a50a58dfa0920aebd865e008214986f1b7c578"; \
    UBLOCK_ORIGIN_XPI="/tmp/ublock_origin-${UBLOCK_ORIGIN_VERSION}.xpi"; \
    UBLOCK_ORIGIN_DIR="/root/.cache/camoufox/addons/UBO"; \
    curl -fsSL "https://addons.mozilla.org/firefox/downloads/file/4888680/ublock_origin-${UBLOCK_ORIGIN_VERSION}.xpi" \
        -o "${UBLOCK_ORIGIN_XPI}"; \
    echo "${UBLOCK_ORIGIN_SHA256}  ${UBLOCK_ORIGIN_XPI}" | sha256sum -c -; \
    rm -rf "${UBLOCK_ORIGIN_DIR}"; \
    mkdir -p "${UBLOCK_ORIGIN_DIR}"; \
    unzip -q "${UBLOCK_ORIGIN_XPI}" -d "${UBLOCK_ORIGIN_DIR}"; \
    test -f /root/.cache/camoufox/addons/UBO/manifest.json; \
    node -e "const m=require('/root/.cache/camoufox/addons/UBO/manifest.json'); if (m.version !== '${UBLOCK_ORIGIN_VERSION}') throw new Error('unexpected uBlock Origin version');"; \
    rm -f "${UBLOCK_ORIGIN_XPI}"

ENV CAMOFOX_CRASH_REPORT_ENABLED=false
ENV CAMOFOX_GEOIP=false
ENV CAMOFOX_INTERACTIVE=off
ENV CAMOFOX_DISABLE_DEFAULT_ADDONS=false
ENV CAMOFOX_ADDONS=

# GHCR's PyInstaller binary extracts shared libraries into /tmp and cannot
# run with Spartan's noexec tmpfs. Reuse upstream's installer and pinned
# portable Python zipapp instead; do not weaken the runtime mount policy.
RUN YT_DLP_VERSION=2026.08.19 YT_DLP_ASSET=yt-dlp \
    YT_DLP_SHA256=1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6 \
    sh plugins/youtube/post-install.sh \
    && yt-dlp --version
RUN command -v python3 >/dev/null && python3 --version >/dev/null

COPY spartan-health.mjs /app/spartan-health.mjs
COPY spartan-lifecycle.mjs /app/spartan-lifecycle.mjs
COPY patch-addon-readiness.py /tmp/spartan-camofox-patch-addon-readiness.py
RUN python3 /tmp/spartan-camofox-patch-addon-readiness.py \
    && rm /tmp/spartan-camofox-patch-addon-readiness.py
COPY patch-server.js /tmp/spartan-camofox-patch-server.js
RUN node /tmp/spartan-camofox-patch-server.js && rm /tmp/spartan-camofox-patch-server.js
COPY patch-vnc-readiness.js /tmp/spartan-camofox-patch-vnc-readiness.js
RUN node /tmp/spartan-camofox-patch-vnc-readiness.js \
    && rm /tmp/spartan-camofox-patch-vnc-readiness.js
COPY patch-log-redaction.js /tmp/spartan-camofox-patch-log-redaction.js
RUN node /tmp/spartan-camofox-patch-log-redaction.js && rm /tmp/spartan-camofox-patch-log-redaction.js
COPY patch-persistence.js /tmp/spartan-camofox-patch-persistence.js
RUN node /tmp/spartan-camofox-patch-persistence.js && rm /tmp/spartan-camofox-patch-persistence.js
COPY patch-operator-lease.js /tmp/spartan-camofox-patch-operator-lease.js
RUN node /tmp/spartan-camofox-patch-operator-lease.js && rm /tmp/spartan-camofox-patch-operator-lease.js
COPY patch-operator-lease-api.js /tmp/spartan-camofox-patch-operator-lease-api.js
RUN node /tmp/spartan-camofox-patch-operator-lease-api.js && rm /tmp/spartan-camofox-patch-operator-lease-api.js
COPY patch-memory-pressure.js /tmp/spartan-camofox-patch-memory-pressure.js
RUN node /tmp/spartan-camofox-patch-memory-pressure.js && rm /tmp/spartan-camofox-patch-memory-pressure.js
COPY patch-vault-evaluate.js /tmp/spartan-camofox-patch-vault-evaluate.js
RUN node /tmp/spartan-camofox-patch-vault-evaluate.js && rm /tmp/spartan-camofox-patch-vault-evaluate.js
RUN grep -q "SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1" server.js \
    && grep -q "SPARTAN_PERSISTENCE_DURABILITY_V2" lib/persistence.js \
    && grep -q "SPARTAN_OPERATOR_LEASE_V2" server.js \
    && node --check server.js \
    && node --check lib/persistence.js \
    && node --check plugins/persistence/index.js

# Keep the browser API under an independent PID-1 watchdog. The supervisor
# receives its internal loopback credential through a 0600 file, not argv.
COPY supervisor.py /usr/local/bin/spartan-camofox-supervisor.py
RUN chmod 0755 /usr/local/bin/spartan-camofox-supervisor.py
CMD ["python3", "/usr/local/bin/spartan-camofox-supervisor.py", "run"]
