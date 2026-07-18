#!/bin/sh
# Merge public and private whitelist fragments before starting Tinyproxy.

set -eu

PUBLIC_WHITELIST="${SPARTAN_WHITELIST_PUBLIC:-/etc/tinyproxy/whitelist.public.txt}"
PRIVATE_WHITELIST="${SPARTAN_WHITELIST_PRIVATE:-/etc/tinyproxy/whitelist.private.txt}"
COMBINED_WHITELIST="${SPARTAN_WHITELIST_COMBINED:-/tmp/spartan-whitelist.txt}"
REFRESH_TIME="${SPARTAN_WHITELIST_REFRESH_TIME:-02:00}"
REFRESH_TZ="${SPARTAN_WHITELIST_REFRESH_TZ:-Europe/Madrid}"
MAX_POLICY_BYTES="${SPARTAN_WHITELIST_MAX_BYTES:-262144}"
MAX_POLICY_ENTRIES="${SPARTAN_WHITELIST_MAX_ENTRIES:-2048}"
TMP_WHITELIST="${COMBINED_WHITELIST}.tmp"
NEXT_WHITELIST="${COMBINED_WHITELIST}.next"
TMP_DOMAINS="${COMBINED_WHITELIST}.domains.tmp"
ROLLBACK_WHITELIST="${COMBINED_WHITELIST}.rollback"
proxy_pid=""
refresh_pid=""
supervisor_pid="$$"
refresh_performed=0
had_whitelist=0
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
    echo "[outbound-proxy] Invalid SPARTAN_WHITELIST_REFRESH_TIME; expected HH:MM in 00:00-23:59" >&2
    return 1
  fi
  if ! valid_refresh_timezone "$REFRESH_TZ"; then
    echo "[outbound-proxy] Invalid SPARTAN_WHITELIST_REFRESH_TZ; expected an installed traversal-free timezone" >&2
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
    echo "[outbound-proxy] Invalid SPARTAN_WHITELIST_MAX_BYTES; expected 1-16777216" >&2
    return 1
  fi
  if ! valid_policy_limit "$MAX_POLICY_ENTRIES" 65536; then
    echo "[outbound-proxy] Invalid SPARTAN_WHITELIST_MAX_ENTRIES; expected 1-65536" >&2
    return 1
  fi
}

validate_policy_input_file() {
  file="$1"
  [ -f "$file" ] || return 0
  if ! bytes="$(wc -c < "$file" | tr -d '[:space:]')"; then
    echo "[outbound-proxy] Could not measure whitelist input" >&2
    return 1
  fi
  case "$bytes" in ""|*[!0-9]*)
    echo "[outbound-proxy] Invalid whitelist byte count" >&2
    return 1
    ;;
  esac
  if [ "$bytes" -gt "$MAX_POLICY_BYTES" ]; then
    echo "[outbound-proxy] Whitelist input exceeds byte limit ${MAX_POLICY_BYTES}" >&2
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

filter_pattern() {
  domain="$1"
  escaped="$(printf '%s' "$domain" | sed 's/\./\\./g')"
  case "$domain" in
    *[!0-9.]*)
      printf '^\\(https\\?://\\)\\?\\([^/@]*@\\)\\?\\([^.\\/:]*\\.\\)*%s\\(:[0-9][0-9]*\\)\\?\\(/.*\\)\\?$\n' "$escaped"
      ;;
    *)
      printf '^\\(https\\?://\\)\\?%s\\(:[0-9][0-9]*\\)\\?\\(/.*\\)\\?$\n' "$escaped"
      ;;
  esac
}

cleanup_whitelist_staging() {
  rm -f "$TMP_DOMAINS" "$TMP_WHITELIST" "$NEXT_WHITELIST" 2>/dev/null || true
}

stage_whitelist() {
  cleanup_whitelist_staging
  validate_policy_inputs || return 1

  if ! {
    normalize_whitelist "$PUBLIC_WHITELIST"
    normalize_whitelist "$PRIVATE_WHITELIST"
  } | sort -u > "$TMP_DOMAINS"; then
    cleanup_whitelist_staging
    return 1
  fi

  if ! generated_count="$(wc -l < "$TMP_DOMAINS" | tr -d '[:space:]')"; then
    cleanup_whitelist_staging
    return 1
  fi
  case "$generated_count" in ""|*[!0-9]*)
    cleanup_whitelist_staging
    return 1
    ;;
  esac
  if [ "$generated_count" -gt "$MAX_POLICY_ENTRIES" ]; then
    echo "[outbound-proxy] Normalized whitelist exceeds entry limit ${MAX_POLICY_ENTRIES}" >&2
    cleanup_whitelist_staging
    return 1
  fi

  : > "$TMP_WHITELIST" || {
    cleanup_whitelist_staging
    return 1
  }
  while IFS= read -r domain || [ -n "$domain" ]; do
    [ -n "$domain" ] || continue
    filter_pattern "$domain"
  done < "$TMP_DOMAINS" >> "$TMP_WHITELIST"
  if ! sort -u "$TMP_WHITELIST" > "$NEXT_WHITELIST"; then
    cleanup_whitelist_staging
    return 1
  fi
  rm -f "$TMP_DOMAINS" "$TMP_WHITELIST"
}

backup_whitelist() {
  had_whitelist=0
  rm -f "$ROLLBACK_WHITELIST" 2>/dev/null || true
  if [ -f "$COMBINED_WHITELIST" ]; then
    if ! cp "$COMBINED_WHITELIST" "$ROLLBACK_WHITELIST"; then
      echo "[outbound-proxy] Could not stage runtime whitelist rollback" >&2
      return 1
    fi
    had_whitelist=1
  fi
}

rollback_whitelist() {
  status=0
  if [ "$had_whitelist" -eq 1 ]; then
    if [ ! -f "$ROLLBACK_WHITELIST" ] || ! mv "$ROLLBACK_WHITELIST" "$COMBINED_WHITELIST"; then
      echo "[outbound-proxy] Runtime whitelist rollback failed" >&2
      status=1
    fi
  elif ! rm -f "$COMBINED_WHITELIST"; then
    echo "[outbound-proxy] Runtime whitelist rollback failed" >&2
    status=1
  fi
  cleanup_whitelist_staging
  return "$status"
}

commit_whitelist() {
  if ! rm -f "$ROLLBACK_WHITELIST"; then
    echo "[outbound-proxy] Could not finalize runtime whitelist transaction" >&2
    return 1
  fi
  had_whitelist=0
  echo "[outbound-proxy] Loaded ${generated_count} whitelisted domains from public/private fragments"
}

generate_whitelist() {
  stage_whitelist || return 1
  if ! backup_whitelist; then
    cleanup_whitelist_staging
    return 1
  fi
  if ! mv "$NEXT_WHITELIST" "$COMBINED_WHITELIST"; then
    echo "[outbound-proxy] Runtime whitelist install failed" >&2
    rollback_whitelist || true
    return 1
  fi
}

policy_freshness_lost() {
  proxy_pid="$1"
  echo "[outbound-proxy] Policy freshness lost; terminating so restart must re-enter fail-closed startup" >&2
  if ! kill -TERM "$supervisor_pid" 2>/dev/null; then
    kill "$proxy_pid" 2>/dev/null || true
  fi
  return 1
}

reload_whitelist() {
  proxy_pid="$1"
  if ! generate_whitelist; then
    echo "[outbound-proxy] Whitelist reload failed; keeping previous runtime whitelist" >&2
    return 1
  fi
  if [ -z "$proxy_pid" ] || ! kill -0 "$proxy_pid" 2>/dev/null; then
    echo "[outbound-proxy] Whitelist regenerated; Tinyproxy child is not running" >&2
    rollback_whitelist || true
    return 1
  fi
  if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "scheduled-refresh" ] && [ "${SPARTAN_WHITELIST_TEST_SIGNAL_FAILURE:-0}" = "1" ]; then
    echo "[outbound-proxy] Whitelist regenerated but Tinyproxy reload signaling failed" >&2
    rollback_whitelist || true
    return 1
  fi
  if ! kill -USR1 "$proxy_pid" 2>/dev/null; then
    echo "[outbound-proxy] Whitelist regenerated but Tinyproxy reload signaling failed" >&2
    rollback_whitelist || true
    return 1
  fi
  if ! commit_whitelist; then
    rollback_whitelist || true
    return 1
  fi
}

scheduled_refresh() {
  proxy_pid="$1"
  refresh_performed=0
  if ! current_time="$(current_refresh_time)"; then
    echo "[outbound-proxy] Could not determine the scheduled refresh time" >&2
    policy_freshness_lost "$proxy_pid"
    return 1
  fi
  if [ "$current_time" != "$REFRESH_TIME" ]; then
    return 0
  fi

  refresh_performed=1
  echo "[outbound-proxy] Refreshing whitelist for scheduled window ${REFRESH_TIME} ${REFRESH_TZ}"
  if ! reload_whitelist "$proxy_pid"; then
    policy_freshness_lost "$proxy_pid"
    return 1
  fi
}

refresh_loop() {
  proxy_pid="$1"
  while kill -0 "$proxy_pid" 2>/dev/null; do
    if ! sleep 60; then
      policy_freshness_lost "$proxy_pid"
      return 1
    fi
    if ! scheduled_refresh "$proxy_pid"; then
      return 1
    fi
    if [ "$refresh_performed" -eq 1 ]; then
      if ! sleep 61; then
        policy_freshness_lost "$proxy_pid"
        return 1
      fi
    fi
  done
}

handle_reload() {
  if ! reload_whitelist "$proxy_pid"; then
    policy_freshness_lost "$proxy_pid" || true
  fi
}

shutdown() {
  if [ -n "$proxy_pid" ]; then
    kill "$proxy_pid" 2>/dev/null || true
  fi
  if [ -n "$refresh_pid" ]; then
    kill "$refresh_pid" 2>/dev/null || true
  fi
  if [ -n "$proxy_pid" ]; then
    wait "$proxy_pid" 2>/dev/null || true
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

generate_whitelist
commit_whitelist

if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "generate" ]; then
  cat "$COMBINED_WHITELIST"
  exit 0
fi

/bin/sh /app/entrypoint.sh &
proxy_pid="$!"
refresh_loop "$proxy_pid" &
refresh_pid="$!"

set +e
wait "$proxy_pid"
status="$?"
if [ -n "$refresh_pid" ]; then
  kill "$refresh_pid" 2>/dev/null || true
  wait "$refresh_pid" 2>/dev/null || true
fi
exit "$status"
