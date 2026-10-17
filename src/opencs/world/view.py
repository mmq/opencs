"""What one client has been told (I §7.2, I §7.3; DESIGN §3.5).

The client keeps a registry it never unlinks from and answers a second create for the same id with a
duplicate, so the server must remember which entities each client holds: whom it has been introduced
to, which towers it has seen. Audiences like ``ToHolders`` resolve against this, and a late joiner's
sync is built from it. It holds the client's own pod, the pods it has been introduced to and any
other entity whose create it was sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LegCount:
    """One client's count of a guppy's legs (I §9.4.1, I §7.3). A client reports every leg it
    completes, once, in order, so its n-th report is about leg n: ``reports`` is how many it has
    made, and its next is leg number ``reports``."""

    reports: int = 0


@dataclass(slots=True)
class ClientView:
    """One player's client: when it started (the id-reuse guard, I §6.2, II §8) and what it
    holds."""

    player_id: int
    joined_at: float
    held: set[int] = field(default_factory=set)
    #: Guppy id -> this client's leg count, for each guppy it holds (I §7.3).
    legs: dict[int, LegCount] = field(default_factory=dict)

    def hold(self, entity_id: int) -> None:
        """The client has been told this entity exists."""
        self.held.add(entity_id)

    def forget(self, entity_id: int) -> None:
        """The client no longer holds it (it was deleted)."""
        self.held.discard(entity_id)
        self.legs.pop(entity_id, None)

    def holds(self, entity_id: int) -> bool:
        return entity_id in self.held
