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
- Native S256 PKCE and stdin callback input are reused. Before consent, choose
  Ordenador or Móvil/Termius. Mobile mode restores an omitted scheme only for
  the current session's expected callback prefix; host/path/state checks remain.
  The reference still includes HTTP/HTTPS and trailing slash. Loopback HTTP
  failing to load on a phone is expected; copy the final address anyway.
- The helper is implemented to publish refresh tokens, granted scopes and the
  current Workspace token/expiry to the native files, with one owner-only
  `.sg-previous` backup per destination. The operator now reports successful
  mobile login; independent native readback remains pending. No Google revocation
  or cloud mutation was performed by the implementation checks.

## Resource evidence and limits

Native batch group updates caused a reconnect storm approaching the existing
hub memory cap. A single MCPHub-only restart collected the overlapping children;
limits were not raised. Subsequent repeated individual Google reloads settled to
one GTM and one Analytics child in under2seconds, around670MiB combined hub use
under its unchanged1.5GiB cap, with no post-recovery max/OOM events. This is bounded
observed lifecycle evidence, not a universal guarantee about upstream memory.
The ordinary login performs only individual reloads, not group/config rebuilds.

## Operator confirmation and remaining acceptance

The operator reports that the login completed successfully from mobile. The
follow-up desktop/mobile selector addresses the browser omitting `http://` when
copying localhost; no new consent, token change or service restart is needed to
install this UI adjustment. On the next normal login, select2 for Móvil/Termius.
Minimal native reads and installed-grant readback remain separate from this user
confirmation. Enabling tools alone does not prove Google resource access; account
permissions, scopes and backend-supported operations all apply. Reconnect existing
MCP client sessions for refreshed catalogs. Never send callback URLs, passwords,
refresh tokens or ADC through chat.

There is no new daemon, periodic token-refresh job, callback listener or HTTP
service. Clients refresh access tokens under demand; Google Testing/revocation/
session policy can still require human reauthorization. Runtime recovery and
remaining acceptance belong to the existing Google-login task package.
