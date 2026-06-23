ARG CAMOFOX_BASE_IMAGE=ghcr.io/jo-inc/camofox-browser:1.11.2
FROM ${CAMOFOX_BASE_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends x11vnc novnc websockify yt-dlp \
    && rm -rf /var/lib/apt/lists/*

# Camofox/camoufox-js downloads its default uBlock Origin addon the first time
# a browser launches. Spartan Gate keeps the runtime service on the internal
# network, so prefetch the addon at build time instead of allowing direct
# runtime egress.
RUN node --input-type=module -e "import { maybeDownloadAddons } from 'camoufox-js/dist/addons.js'; await maybeDownloadAddons({ UBO: 'https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi' });"

ENV CAMOFOX_CRASH_REPORT_ENABLED=false
ENV CAMOFOX_GEOIP=false
ENV CAMOFOX_ADDONS=

RUN command -v yt-dlp >/dev/null && yt-dlp --version >/dev/null

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
