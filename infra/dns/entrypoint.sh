#!/bin/sh
# DNS relay with domain whitelisting.
# It reads the public whitelist plus an optional private whitelist fragment.
# All other queries return REFUSED, which blocks DNS exfiltration from Hermes.

set -e

PUBLIC_WHITELIST="${SPARTAN_DNS_WHITELIST_PUBLIC:-/etc/dns-whitelist.public.txt}"
PRIVATE_WHITELIST="${SPARTAN_DNS_WHITELIST_PRIVATE:-/etc/dns-whitelist.private.txt}"
REFRESH_TIME="${SPARTAN_WHITELIST_REFRESH_TIME:-02:00}"
REFRESH_TZ="${SPARTAN_WHITELIST_REFRESH_TZ:-Europe/Madrid}"
MAX_POLICY_BYTES="${SPARTAN_WHITELIST_MAX_BYTES:-262144}"
MAX_POLICY_ENTRIES="${SPARTAN_WHITELIST_MAX_ENTRIES:-2048}"
if [ -n "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" ] && [ -n "${SPARTAN_WHITELIST_TEST_ARTIFACT_DIR:-}" ]; then
  DOMAINS="${SPARTAN_WHITELIST_TEST_ARTIFACT_DIR}/domains.txt"
  SERVERS="${SPARTAN_WHITELIST_TEST_ARTIFACT_DIR}/servers.conf"
  CONF="${SPARTAN_WHITELIST_TEST_ARTIFACT_DIR}/dnsmasq.conf"
else
  DOMAINS="/tmp/dns-whitelist-domains.txt"
  SERVERS="/tmp/dnsmasq-whitelist.servers"
  CONF="/tmp/dnsmasq-whitelist.conf"
fi
TMP_DOMAINS="${DOMAINS}.tmp"
TMP_SERVERS="${SERVERS}.tmp"
ROLLBACK_DOMAINS="${DOMAINS}.rollback"
ROLLBACK_SERVERS="${SERVERS}.rollback"
dns_pid=""
refresh_pid=""
supervisor_pid="$$"
refresh_performed=0
had_domains=0
had_servers=0
generated_count=0

current_epoch() {
  if [ -n "${SPARTAN_WHITELIST_NOW:-}" ]; then
    printf '%s\n' "$SPARTAN_WHITELIST_NOW"
  else
    date +%s
  fi
}

valid_refresh_time() {
  case "$1" in
    [01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) return 0 ;;
    *) return 1 ;;
  esac
}

valid_refresh_timezone() {
  timezone="$1"
  case "$timezone" in
    ""|/*|*/|*//*|*..*|*\\*) return 1 ;;
  esac
  printf '%s\n' "$timezone" | grep -Eq '^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$' || return 1
  [ -f "/usr/share/zoneinfo/$timezone" ]
}

validate_refresh_config() {
  if ! valid_refresh_time "$REFRESH_TIME"; then
    echo "[dns-relay] Invalid SPARTAN_WHITELIST_REFRESH_TIME; expected HH:MM in 00:00-23:59" >&2
    return 1
  fi
  if ! valid_refresh_timezone "$REFRESH_TZ"; then
    echo "[dns-relay] Invalid SPARTAN_WHITELIST_REFRESH_TZ; expected an installed traversal-free timezone" >&2
    return 1
  fi
}

valid_policy_limit() {
  value="$1"
  maximum="$2"
  case "$value" in ""|*[!0-9]*) return 1 ;; esac
  [ "${#value}" -le 8 ] || return 1
  [ "$value" -ge 1 ] && [ "$value" -le "$maximum" ]
}

validate_policy_config() {
  if ! valid_policy_limit "$MAX_POLICY_BYTES" 16777216; then
    echo "[dns-relay] Invalid SPARTAN_WHITELIST_MAX_BYTES; expected 1-16777216" >&2
    return 1
  fi
  if ! valid_policy_limit "$MAX_POLICY_ENTRIES" 65536; then
    echo "[dns-relay] Invalid SPARTAN_WHITELIST_MAX_ENTRIES; expected 1-65536" >&2
    return 1
  fi
}

validate_policy_input_file() {
  file="$1"
  [ -f "$file" ] || return 0
  if ! bytes="$(wc -c < "$file" | tr -d '[:space:]')"; then
    echo "[dns-relay] Could not measure whitelist input" >&2
    return 1
  fi
  case "$bytes" in ""|*[!0-9]*)
    echo "[dns-relay] Invalid whitelist byte count" >&2
    return 1
    ;;
  esac
  if [ "$bytes" -gt "$MAX_POLICY_BYTES" ]; then
    echo "[dns-relay] Whitelist input exceeds byte limit ${MAX_POLICY_BYTES}" >&2
    return 1
  fi
}

validate_policy_inputs() {
  validate_policy_input_file "$PUBLIC_WHITELIST" || return 1
  validate_policy_input_file "$PRIVATE_WHITELIST" || return 1
}

current_refresh_time() {
  if [ -n "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" ] && [ -n "${SPARTAN_WHITELIST_REFRESH_NOW:-}" ]; then
    printf '%s\n' "$SPARTAN_WHITELIST_REFRESH_NOW"
  else
    TZ="$REFRESH_TZ" date '+%H:%M'
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

cleanup_server_staging() {
  rm -f "$TMP_DOMAINS" "$TMP_SERVERS" 2>/dev/null || true
}

stage_servers() {
  cleanup_server_staging
  validate_policy_inputs || return 1

  if ! {
    normalize_whitelist "$PUBLIC_WHITELIST"
    normalize_whitelist "$PRIVATE_WHITELIST"
  } | sort -u > "$TMP_DOMAINS"; then
    cleanup_server_staging
    return 1
  fi

  if ! generated_count="$(wc -l < "$TMP_DOMAINS" | tr -d '[:space:]')"; then
    cleanup_server_staging
    return 1
  fi
  case "$generated_count" in ""|*[!0-9]*)
    cleanup_server_staging
    return 1
    ;;
  esac
  if [ "$generated_count" -gt "$MAX_POLICY_ENTRIES" ]; then
    echo "[dns-relay] Normalized whitelist exceeds entry limit ${MAX_POLICY_ENTRIES}" >&2
    cleanup_server_staging
    return 1
  fi

  : > "$TMP_SERVERS" || {
    cleanup_server_staging
    return 1
  }
  while IFS= read -r domain || [ -n "$domain" ]; do
    [ -n "$domain" ] || continue
    echo "server=/${domain}/1.1.1.1" >> "$TMP_SERVERS"
    echo "server=/${domain}/8.8.8.8" >> "$TMP_SERVERS"
  done < "$TMP_DOMAINS"

  if ! server_lines="$(wc -l < "$TMP_SERVERS" | tr -d '[:space:]')"; then
    cleanup_server_staging
    return 1
  fi
  case "$server_lines" in ""|*[!0-9]*)
    cleanup_server_staging
    return 1
    ;;
  esac
  if [ "$server_lines" -ne "$((generated_count * 2))" ]; then
    echo "[dns-relay] Generated DNS artifact validation failed" >&2
    cleanup_server_staging
    return 1
  fi
}

backup_server_artifacts() {
  had_domains=0
  had_servers=0
  rm -f "$ROLLBACK_DOMAINS" "$ROLLBACK_SERVERS" 2>/dev/null || true
  if [ -f "$DOMAINS" ]; then
    if ! cp "$DOMAINS" "$ROLLBACK_DOMAINS"; then
      echo "[dns-relay] Could not stage domain-list rollback" >&2
      return 1
    fi
    had_domains=1
  fi
  if [ -f "$SERVERS" ]; then
    if ! cp "$SERVERS" "$ROLLBACK_SERVERS"; then
      echo "[dns-relay] Could not stage server-list rollback" >&2
      rm -f "$ROLLBACK_DOMAINS" 2>/dev/null || true
      return 1
    fi
    had_servers=1
  fi
}

restore_server_artifact() {
  had_artifact="$1"
  rollback_file="$2"
  live_file="$3"
  if [ "$had_artifact" -eq 1 ]; then
    [ -f "$rollback_file" ] && mv "$rollback_file" "$live_file"
  else
    rm -f "$live_file"
  fi
}

rollback_server_artifacts() {
  status=0
  if ! restore_server_artifact "$had_domains" "$ROLLBACK_DOMAINS" "$DOMAINS"; then
    echo "[dns-relay] Domain-list rollback failed" >&2
    status=1
  fi
  if ! restore_server_artifact "$had_servers" "$ROLLBACK_SERVERS" "$SERVERS"; then
    echo "[dns-relay] Server-list rollback failed" >&2
    status=1
  fi
  cleanup_server_staging
  return "$status"
}

commit_server_artifacts() {
  if ! rm -f "$ROLLBACK_DOMAINS" "$ROLLBACK_SERVERS"; then
    echo "[dns-relay] Could not finalize DNS policy transaction" >&2
    return 1
  fi
  had_domains=0
  had_servers=0
  echo ""
  echo "[dns-relay] Loaded ${generated_count} whitelisted domains from public/private fragments"
  echo "[dns-relay] All other DNS queries will be REFUSED"
  echo ""
}

install_server_artifact() {
  install_index="$1"
  source_file="$2"
  live_file="$3"
  if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "scheduled-refresh" ] &&
     [ "${SPARTAN_WHITELIST_TEST_FAIL_INSTALL_AT:-0}" = "$install_index" ]; then
    return 1
  fi
  mv "$source_file" "$live_file"
}

generate_servers() {
  stage_servers || return 1
  if ! backup_server_artifacts; then
    cleanup_server_staging
    return 1
  fi
  if ! install_server_artifact 1 "$TMP_DOMAINS" "$DOMAINS"; then
    echo "[dns-relay] DNS policy install failed at artifact 1" >&2
    rollback_server_artifacts || true
    return 1
  fi
  if ! install_server_artifact 2 "$TMP_SERVERS" "$SERVERS"; then
    echo "[dns-relay] DNS policy install failed at artifact 2" >&2
    rollback_server_artifacts || true
    return 1
  fi
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

policy_freshness_lost() {
  dns_pid="$1"
  echo "[dns-relay] Policy freshness lost; terminating so restart must re-enter fail-closed startup" >&2
  if ! kill -TERM "$supervisor_pid" 2>/dev/null; then
    kill "$dns_pid" 2>/dev/null || true
  fi
  return 1
}

reload_servers() {
  dns_pid="$1"
  if ! generate_servers; then
    echo "[dns-relay] Whitelist reload failed; keeping previous runtime whitelist" >&2
    return 1
  fi
  if [ -z "$dns_pid" ] || ! kill -0 "$dns_pid" 2>/dev/null; then
    echo "[dns-relay] Whitelist regenerated; dnsmasq child is not running" >&2
    rollback_server_artifacts || true
    return 1
  fi
  if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "scheduled-refresh" ] && [ "${SPARTAN_WHITELIST_TEST_SIGNAL_FAILURE:-0}" = "1" ]; then
    echo "[dns-relay] Whitelist regenerated but dnsmasq reload signaling failed" >&2
    rollback_server_artifacts || true
    return 1
  fi
  if ! kill -HUP "$dns_pid" 2>/dev/null; then
    echo "[dns-relay] Whitelist regenerated but dnsmasq reload signaling failed" >&2
    rollback_server_artifacts || true
    return 1
  fi
  if ! commit_server_artifacts; then
    rollback_server_artifacts || true
    return 1
  fi
}

scheduled_refresh() {
  dns_pid="$1"
  refresh_performed=0
  if ! current_time="$(current_refresh_time)"; then
    echo "[dns-relay] Could not determine the scheduled refresh time" >&2
    policy_freshness_lost "$dns_pid"
    return 1
  fi
  if [ "$current_time" != "$REFRESH_TIME" ]; then
    return 0
  fi

  refresh_performed=1
  echo "[dns-relay] Refreshing whitelist for scheduled window ${REFRESH_TIME} ${REFRESH_TZ}"
  if ! reload_servers "$dns_pid"; then
    policy_freshness_lost "$dns_pid"
    return 1
  fi
}

refresh_loop() {
  dns_pid="$1"
  while kill -0 "$dns_pid" 2>/dev/null; do
    if ! sleep 60; then
      policy_freshness_lost "$dns_pid"
      return 1
    fi
    if ! scheduled_refresh "$dns_pid"; then
      return 1
    fi
    if [ "$refresh_performed" -eq 1 ]; then
      if ! sleep 61; then
        policy_freshness_lost "$dns_pid"
        return 1
      fi
    fi
  done
}

handle_reload() {
  if ! reload_servers "$dns_pid"; then
    policy_freshness_lost "$dns_pid" || true
  fi
}

shutdown() {
  if [ -n "$dns_pid" ]; then
    kill "$dns_pid" 2>/dev/null || true
  fi
  if [ -n "$refresh_pid" ]; then
    kill "$refresh_pid" 2>/dev/null || true
  fi
  if [ -n "$dns_pid" ]; then
    wait "$dns_pid" 2>/dev/null || true
  fi
  exit 143
}

trap 'handle_reload' USR1
trap 'shutdown' INT TERM

validate_refresh_config
validate_policy_config

case "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" in
  validate)
    exit 0
    ;;
  scheduled-refresh)
    scheduled_refresh "${SPARTAN_WHITELIST_TEST_CHILD_PID:-99999999}"
    exit "$?"
    ;;
esac

generate_servers
commit_server_artifacts
write_config

if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "generate" ]; then
  cat "$DOMAINS"
  exit 0
fi

dnsmasq --conf-file="$CONF" &
dns_pid="$!"
refresh_loop "$dns_pid" &
refresh_pid="$!"

set +e
wait "$dns_pid"
status="$?"
if [ -n "$refresh_pid" ]; then
  kill "$refresh_pid" 2>/dev/null || true
  wait "$refresh_pid" 2>/dev/null || true
fi
exit "$status"
