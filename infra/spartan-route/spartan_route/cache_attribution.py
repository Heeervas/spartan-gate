"""Request-local selection observations; never credentials or persistent state."""
from contextvars import ContextVar
from dataclasses import dataclass

LEASE: ContextVar[str] = ContextVar("codex_cache_lease", default="none")
HEADER_NAMES = ("x-spartan-route-slot", "x-spartan-route-lease")


def begin_selection(affinity_key: str | None, mode: str) -> None:
    LEASE.set("new" if affinity_key and mode != "off" else "none")


@dataclass(frozen=True)
class SelectionAttribution:
    slot_index: int | None = None
    account_key: str | None = None  # Internal result metadata, never a header.
    lease: str = "none"

    @classmethod
    def capture(cls, slot, auth):
        # Copy before any upstream await or lease mutation, including cleanup.
        return cls(slot.index, auth.account_key, LEASE.get())

    def apply(self, result):
        if self.slot_index is not None:
            result.slot_index = self.slot_index
            result.account_key = self.account_key
            result.headers.update({HEADER_NAMES[0]: str(self.slot_index), HEADER_NAMES[1]: self.lease})
        return result
