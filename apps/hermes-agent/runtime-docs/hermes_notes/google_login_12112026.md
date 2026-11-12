# Unified Google login — 12 November 2026

## Applied

- GogCLI0.48.0 is installed as one checksum-verified persistent binary used by
  Hermes and the host operator wizard. The image pin/checksums match; no Hermes
  rebuild or container recreation occurred. Install without copying a host file's
  SELinux label into the container bind; only the selected binary's label needed
  alignment with its destination directory.
- Official Analytics MCP1.1.0 runs in its own protected versioned environment
  with native `--allow-write`. Its13 tools include annotation, custom-dimension
  and key-event writes. GTM's seven native tools and supported write/publish gates
  are enabled; native dry-run/confirmation contracts remain unchanged.
- Existing selected client groups expose the exact Google tools, not wildcard
  grants. All three existing native client keys discover the tools and remain
  denied management access. Other client keys and permissions are preserved.
- `sg-gog-login` is a small terminal wizard. It reuses one consent for compatible
  client/account destinations; other clients get separate steps. The displayed
  account is authoritative. `--skip-workspace` leaves native Workspace alone;
  `--check` refreshes/redistributes without new consent.
- Native S256 PKCE and stdin callback input are reused. The wizard displays the
  actual callback prefix, including HTTP/HTTPS and trailing slash. Loopback HTTP
  failing to load on a phone is expected; copy the complete final address anyway.
- The helper is implemented to publish refresh tokens, granted scopes and the
  current Workspace token/expiry to the native files, with one owner-only
  `.sg-previous` backup per destination. Actual publication awaits the new
  consent; no Google revocation or cloud mutation was performed.

## Resource evidence and limits

Native batch group updates caused a reconnect storm approaching the existing
hub memory cap. A single MCPHub-only restart collected the overlapping children;
limits were not raised. Subsequent repeated individual Google reloads settled to
one GTM and one Analytics child in under2seconds, around670MiB combined hub use
under its unchanged1.5GiB cap, with no post-recovery max/OOM events. This is bounded
observed lifecycle evidence, not a universal guarantee about upstream memory.
The ordinary login performs only individual reloads, not group/config rebuilds.

## Pending operator action

Run `sg-gog-login` in the actual SSH/Termius terminal and choose Enter to grant the
new Google scopes. The existing Gog refresh worked, but the new write scopes are
not present yet, so `--check` correctly refuses publication. Then validate mobile
completion and minimal native reads. Enabling tools does not prove Google access;
account permissions, scopes and backend-supported operations all apply. Reconnect
existing MCP client sessions to pick up their refreshed catalogs. Never send the
callback URL, password, refresh token or ADC through chat.

There is no new daemon, periodic token-refresh job, callback listener or HTTP
service. Clients refresh access tokens under demand; Google Testing/revocation/
session policy can still require human reauthorization. Runtime recovery and
remaining acceptance belong to the existing Google-login task package.
