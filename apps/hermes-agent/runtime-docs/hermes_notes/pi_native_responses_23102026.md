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

Named-subagent follow-up: the first adapter assumed UUID session IDs and rejected
mjakl's `subagent.<hash>` IDs. The installed correction shares a stable UUIDv8
identity adapter between binding and usage, preserving native sessions. Actual
subagent creation, retry of the previously failed session, resume and independent
named/ephemeral children passed with the parent process kept alive. Fresh child
launches load the fix automatically; retrying those calls needs no parent restart.
Route required no change or restart for this correction.
