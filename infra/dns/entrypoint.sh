#!/bin/sh
# DNS relay with domain whitelisting.
# It reads the public whitelist plus an optional private whitelist fragment.
# All other queries return REFUSED, which blocks DNS exfiltration from Hermes.

set -e

PUBLIC_WHITELIST="${SPARTAN_DNS_WHITELIST_PUBLIC:-/etc/dns-whitelist.public.txt}"
PRIVATE_WHITELIST="${SPARTAN_DNS_WHITELIST_PRIVATE:-/etc/dns-whitelist.private.txt}"
REFRESH_TIME="${SPARTAN_WHITELIST_REFRESH_TIME:-02:00}"
REFRESH_TZ="${SPARTAN_WHITELIST_REFRESH_TZ:-Europe/Madrid}"
DOMAINS="/tmp/dns-whitelist-domains.txt"
SERVERS="/tmp/dnsmasq-whitelist.servers"
CONF="/tmp/dnsmasq-whitelist.conf"

current_epoch() {
  if [ -n "${SPARTAN_WHITELIST_NOW:-}" ]; then
    printf '%s\n' "$SPARTAN_WHITELIST_NOW"
  else
    date +%s
  fi
}

valid_ipv4() {
  host="$1"
  old_ifs="$IFS"
  IFS=.
  set -- $host
  IFS="$old_ifs"
  [ "$#" -eq 4 ] || return 1
  for octet in "$@"; do
    case "$octet" in ""|*[!0-9]*) return 1 ;; esac
    [ "$octet" -le 255 ] || return 1
  done
}

valid_hostname() {
  host="$1"
  [ "${#host}" -le 253 ] || return 1
  case "$host" in ""|.*|*.|*..*|*[!a-z0-9.-]*) return 1 ;; esac
  old_ifs="$IFS"
  IFS=.
  set -- $host
  IFS="$old_ifs"
  for label in "$@"; do
    [ "${#label}" -le 63 ] || return 1
    case "$label" in ""|-*|*-) return 1 ;; esac
  done
}

valid_host() {
  host="$1"
  case "$host" in
    *:*) return 1 ;;
    *[!0-9.]*|*.*[!0-9.]*) valid_hostname "$host" ;;
    *) valid_ipv4 "$host" ;;
  esac
}

line_is_active() {
  raw_line="$1"
  case "$raw_line" in
    *sg-expires-at=*) ;;
    *) return 0 ;;
  esac

  expiry="${raw_line#*sg-expires-at=}"
  expiry="${expiry%% *}"
  case "$expiry" in ""|*[!0-9]*) return 1 ;; esac
  [ "${#expiry}" -le 10 ] || return 1

  now="$(current_epoch)"
  case "$now" in ""|*[!0-9]*) return 1 ;; esac
  [ "${#now}" -le 10 ] || return 1
  [ "$expiry" -gt "$now" ]
}

normalize_whitelist() {
  file="$1"
  [ -f "$file" ] || return 0

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    if ! line_is_active "$raw_line"; then
      continue
    fi

    domain="${raw_line%%#*}"
    domain="$(printf '%s' "$domain" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    [ -n "$domain" ] || continue

    domain="${domain#http://}"
    domain="${domain#https://}"
    domain="${domain#//}"
    domain="${domain%%/*}"
    domain="${domain%%\?*}"
    domain="${domain%%\#*}"
    case "$domain" in
      *:*:*) continue ;;
      *:*)
        port="${domain##*:}"
        case "$port" in ""|*[!0-9]*) continue ;; esac
        domain="${domain%:*}"
        ;;
    esac
    domain="${domain%.}"

    valid_host "$domain" || continue

    printf '%s\n' "$domain"
  done < "$file"
}

generate_servers() {
  : > "$DOMAINS"
  normalize_whitelist "$PUBLIC_WHITELIST" >> "$DOMAINS"
  normalize_whitelist "$PRIVATE_WHITELIST" >> "$DOMAINS"
  sort -u "$DOMAINS" -o "$DOMAINS"

  : > "$SERVERS"
  count=0
  while IFS= read -r domain || [ -n "$domain" ]; do
    [ -n "$domain" ] || continue
    echo "server=/${domain}/1.1.1.1" >> "$SERVERS"
    echo "server=/${domain}/8.8.8.8" >> "$SERVERS"
    count=$((count + 1))
  done < "$DOMAINS"

  echo ""
  echo "[dns-relay] Loaded ${count} whitelisted domains from public/private fragments"
  echo "[dns-relay] All other DNS queries will be REFUSED"
  echo ""
}

write_config() {
  cat > "$CONF" <<EOF
# Auto-generated from whitelist fragments. Do not edit inside the container.
keep-in-foreground
listen-address=0.0.0.0
bind-interfaces
no-resolv
no-hosts
log-queries
log-facility=-
cache-size=500
servers-file=$SERVERS
EOF
}

refresh_loop() {
  dns_pid="$1"
  while kill -0 "$dns_pid" 2>/dev/null; do
    sleep 60
    current_time="$(TZ="$REFRESH_TZ" date '+%H:%M')"
    if [ "$current_time" = "$REFRESH_TIME" ]; then
      echo "[dns-relay] Refreshing whitelist for scheduled window ${REFRESH_TIME} ${REFRESH_TZ}"
      generate_servers
      kill -HUP "$dns_pid" 2>/dev/null || true
      sleep 61
    fi
  done
}

generate_servers
write_config

if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "generate" ]; then
  cat "$DOMAINS"
  exit 0
fi

dnsmasq --conf-file="$CONF" &
dns_pid="$!"
refresh_loop "$dns_pid" &
refresh_pid="$!"

trap 'kill "$dns_pid" "$refresh_pid" 2>/dev/null || true; wait "$dns_pid" 2>/dev/null || true; exit 143' INT TERM

set +e
wait "$dns_pid"
status="$?"
kill "$refresh_pid" 2>/dev/null || true
wait "$refresh_pid" 2>/dev/null || true
exit "$status"
