FROM node:22-trixie-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src
COPY config ./config
COPY web ./web

RUN npm run build
RUN npm prune --omit=dev

RUN mkdir -p /app/data

FROM gcr.io/distroless/nodejs22-debian13 AS runtime

ARG CLAWROUTE_BUILD_REVISION=unknown

WORKDIR /app

ENV PATH="/nodejs/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COPY --from=build --chown=1000:1000 /app/package.json ./package.json
COPY --from=build --chown=1000:1000 /app/package-lock.json ./package-lock.json
COPY --from=build --chown=1000:1000 /app/node_modules ./node_modules
COPY --from=build --chown=1000:1000 /app/dist ./dist
COPY --from=build --chown=1000:1000 /app/config ./config
COPY --from=build --chown=1000:1000 /app/web ./web
COPY --from=build --chown=1000:1000 /app/data ./data

USER 1000:1000

EXPOSE 18790

ENV NODE_ENV=production
ENV CLAWROUTE_HOST=0.0.0.0
ENV CLAWROUTE_PORT=18790
ENV CLAWROUTE_BUILD_REVISION=${CLAWROUTE_BUILD_REVISION}

CMD ["dist/index.js"]
