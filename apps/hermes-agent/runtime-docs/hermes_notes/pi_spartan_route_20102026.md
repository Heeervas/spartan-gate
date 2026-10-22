# Pi and Spartan Route — integration boundary

Status: integration guidance; no host installation, migration or current runtime
acceptance is asserted by this note.

Pi can discover compatible chat models from Route's authenticated model catalog.
Non-chat protocols must not be advertised through a Chat Completions adapter.
Cached discovery needs an explicit age bound; unpublished context/output/cost
metadata must remain unknown or documented conservative defaults, not billing
claims.

When using the authenticated Caddy gateway, keep its authentication separate from
Route's internal bearer credential. Caddy owns upstream credential injection;
Route owns account selection, renewal and quota handling. Do not import provider
OAuth credentials into the client merely to connect it to Route.

Chat Completions streaming does not implement encrypted native Responses replay.
Preserve that protocol boundary rather than silently claiming reasoning-history
compatibility. Verify discovery, cache expiry, refresh concurrency and a synthetic
multi-turn tool exchange before accepting a client installation. Such validation
requires separate authorized execution; this guidance does not establish it.
