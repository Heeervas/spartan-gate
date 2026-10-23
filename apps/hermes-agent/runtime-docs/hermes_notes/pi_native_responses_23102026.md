# Pi native Responses

Status: implementation candidate; activation and real gateway acceptance pending.

Pi's Route provider can use native Responses for models advertising a supported
Responses realm. The provider delegates to Pi's built-in transport and obtains
server-signed session reasoning bindings without copying provider credentials
or Route's signing secret. Same-session resume retains reasoning; forks and
foreign scopes discard opaque reasoning. Realm changes require a new session.

Route keeps Hermes's existing native Responses validation and adds a separate
Pi signing namespace and native Pi usage accounting. Other catalog models
retain their supported protocol. See `docs/pi-native-responses.md` for the
source contract, installation and verification boundaries.
