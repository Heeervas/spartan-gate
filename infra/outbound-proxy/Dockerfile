FROM ghcr.io/querateam/docker-tinyproxy:latest

RUN apk add --no-cache tzdata

COPY entrypoint.sh /spartan-entrypoint.sh

ENTRYPOINT ["/bin/sh", "/spartan-entrypoint.sh"]
