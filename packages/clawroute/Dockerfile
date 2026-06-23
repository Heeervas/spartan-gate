FROM node:22-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src
COPY config ./config
COPY web ./web

RUN npm run build
RUN npm prune --omit=dev

FROM node:22-slim AS runtime

ARG CLAWROUTE_BUILD_REVISION=unknown

WORKDIR /app

COPY --from=build /app/package.json ./package.json
COPY --from=build /app/package-lock.json ./package-lock.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/config ./config
COPY --from=build /app/web ./web

RUN mkdir -p /app/data && chown -R node:node /app/data

USER node

EXPOSE 18790

ENV NODE_ENV=production
ENV CLAWROUTE_HOST=0.0.0.0
ENV CLAWROUTE_PORT=18790
ENV CLAWROUTE_BUILD_REVISION=${CLAWROUTE_BUILD_REVISION}

CMD ["node", "dist/index.js"]
