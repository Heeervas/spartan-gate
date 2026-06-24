FROM ghcr.io/querateam/docker-tinyproxy:latest@sha256:2fd992bc1727f42ef257251199e046c179d8b3137ad22657431b004c1cce11ff

RUN apk add --no-cache tzdata

COPY entrypoint.sh /spartan-entrypoint.sh

ENTRYPOINT ["/bin/sh", "/spartan-entrypoint.sh"]
