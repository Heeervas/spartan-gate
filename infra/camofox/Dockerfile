ARG CAMOFOX_BASE_IMAGE=ghcr.io/jo-inc/camofox-browser:1.11.2@sha256:826da04c4ec75b3eb450bc7cf2513176ba408f92b862b89f768ca30563171137
FROM ${CAMOFOX_BASE_IMAGE}

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates curl fontconfig unzip x11vnc novnc websockify yt-dlp \
    && rm -rf /var/lib/apt/lists/*

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
ENV CAMOFOX_ADDONS=

RUN command -v yt-dlp >/dev/null && yt-dlp --version >/dev/null
RUN command -v python3 >/dev/null && python3 --version >/dev/null

COPY spartan-health.mjs /app/spartan-health.mjs
COPY spartan-lifecycle.mjs /app/spartan-lifecycle.mjs
COPY patch-server.js /tmp/spartan-camofox-patch-server.js
RUN node /tmp/spartan-camofox-patch-server.js && rm /tmp/spartan-camofox-patch-server.js

# Upstream ships the VNC plugin entry with "enabled": false. The plugin loader
# skips disabled plugins before the plugin can read ENABLE_VNC, so remove only
# that static flag and let ENABLE_VNC control runtime activation.
RUN node -e "const fs=require('fs'); const path='camofox.config.json'; const config=JSON.parse(fs.readFileSync(path,'utf8')); if(!config.plugins?.vnc || config.plugins.vnc.enabled !== false) throw new Error('expected disabled vnc plugin config not found'); delete config.plugins.vnc.enabled; fs.writeFileSync(path, JSON.stringify(config, null, 2) + '\n');"

# Upstream enables camoufox-js geoip whenever a proxy is configured. Spartan
# Gate's default proxy is local Tinyproxy, not a residential proxy with a useful
# geolocation target, and the geoip public-IP probes do not follow the runtime
# proxy path. Keep browser proxying enabled while making geoip opt-in.
RUN node -e "const fs=require('fs'); const path='server.js'; let s=fs.readFileSync(path,'utf8'); const from='geoip: !!launchProxy,'; const to=\"geoip: process.env.CAMOFOX_GEOIP !== 'false' && !!launchProxy,\"; if(!s.includes(from)) throw new Error('expected Camofox geoip launch marker not found'); s=s.replaceAll(from,to); fs.writeFileSync(path,s);"

# Keep the browser API under an independent PID-1 watchdog. The supervisor
# receives its internal loopback credential through a 0600 file, not argv.
COPY supervisor.py /usr/local/bin/spartan-camofox-supervisor.py
RUN chmod 0755 /usr/local/bin/spartan-camofox-supervisor.py
CMD ["python3", "/usr/local/bin/spartan-camofox-supervisor.py", "run"]
