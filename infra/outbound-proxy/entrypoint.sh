#!/bin/sh
# Merge public and private whitelist fragments before starting Tinyproxy.

set -eu

PUBLIC_WHITELIST="${SPARTAN_WHITELIST_PUBLIC:-/etc/tinyproxy/whitelist.public.txt}"
PRIVATE_WHITELIST="${SPARTAN_WHITELIST_PRIVATE:-/etc/tinyproxy/whitelist.private.txt}"
COMBINED_WHITELIST="${SPARTAN_WHITELIST_COMBINED:-/tmp/spartan-whitelist.txt}"
REFRESH_TIME="${SPARTAN_WHITELIST_REFRESH_TIME:-02:00}"
REFRESH_TZ="${SPARTAN_WHITELIST_REFRESH_TZ:-Europe/Madrid}"
TMP_WHITELIST="${COMBINED_WHITELIST}.tmp"

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

generate_whitelist() {
  : > "$TMP_WHITELIST"
  {
    normalize_whitelist "$PUBLIC_WHITELIST"
    normalize_whitelist "$PRIVATE_WHITELIST"
  } | sort -u | while IFS= read -r domain || [ -n "$domain" ]; do
    [ -n "$domain" ] || continue
    filter_pattern "$domain"
  done >> "$TMP_WHITELIST"
  sort -u "$TMP_WHITELIST" > "$COMBINED_WHITELIST"
  rm -f "$TMP_WHITELIST"

  count="$(wc -l < "$COMBINED_WHITELIST" | tr -d '[:space:]')"
  echo "[outbound-proxy] Loaded ${count} whitelisted domains from public/private fragments"
}

refresh_loop() {
  proxy_pid="$1"
  while kill -0 "$proxy_pid" 2>/dev/null; do
    sleep 60
    current_time="$(TZ="$REFRESH_TZ" date '+%H:%M')"
    if [ "$current_time" = "$REFRESH_TIME" ]; then
      echo "[outbound-proxy] Refreshing whitelist for scheduled window ${REFRESH_TIME} ${REFRESH_TZ}"
      generate_whitelist
      kill -USR1 "$proxy_pid" 2>/dev/null || true
      sleep 61
    fi
  done
}

generate_whitelist

if [ "${SPARTAN_WHITELIST_ENTRYPOINT_TEST:-}" = "generate" ]; then
  cat "$COMBINED_WHITELIST"
  exit 0
fi

/bin/sh /app/entrypoint.sh &
proxy_pid="$!"
refresh_loop "$proxy_pid" &
refresh_pid="$!"

trap 'kill "$proxy_pid" "$refresh_pid" 2>/dev/null || true; wait "$proxy_pid" 2>/dev/null || true; exit 143' INT TERM

set +e
wait "$proxy_pid"
status="$?"
kill "$refresh_pid" 2>/dev/null || true
wait "$refresh_pid" 2>/dev/null || true
exit "$status"
