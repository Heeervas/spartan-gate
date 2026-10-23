# Pi native Responses

Status: installed and activated; real gateway acceptance passed.

Pi's Route provider can use native Responses for models advertising a supported
Responses realm. The provider delegates to Pi's built-in transport and obtains
server-signed session reasoning bindings without copying provider credentials
or Route's signing secret. Same-session resume retains reasoning; forks and
foreign scopes discard opaque reasoning. Realm changes require a new session.

Route keeps Hermes's existing native Responses validation and adds a separate
Pi signing namespace and native Pi usage accounting. Other catalog models
retain their supported protocol. See `docs/pi-native-responses.md` for the
source contract, installation and verification boundaries.

The installed Pi 1.3.0 provider and deployed Route source match the reviewed
bytes. A synthetic six-call gateway test passed native streaming, a read-tool
turn, continuation, process restart and encrypted reasoning replay with the
same signed chain. Route recorded all six attempts with no missing usage.
The other stack services retained their existing containers. Existing Pi
processes need a restart to load the new provider/helper modules.
