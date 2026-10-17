"""What one client has been told (I §7.2, I §7.3; DESIGN §3.5).

The client keeps a registry it never unlinks from and answers a second create for the same id with a
duplicate, so the server must remember which entities each client holds: whom it has been introduced
to, which towers it has seen. Audiences like ``ToHolders`` resolve against this, and a late joiner's
sync is built from it. It holds the client's own pod, the pods it has been introduced to and any
other entity whose create it was sent; city-wide state that is not an entity is in ``told``.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field


@dataclass(slots=True)
class LegCount:
    """One client's count of a guppy's legs (I §9.4.1, I §7.3). A client reports every leg it
    completes, once, in order, so its n-th report is about leg n: ``reports`` is how many it has
    made, and its next is leg number ``reports``.

    A client that was introduced after the create leg is *joining*: its copy is at home and its
    reports drive nothing until it is in step with the guppy. ``caught_up`` is the leg last sent to
    it as a catch-up, or None while only the create has been sent.

    ``owed`` is how many reports the client can still make: one for the create and one for each
    destination sent to it since. A report with none owed is out of protocol (a modified client's
    early report) and is ignored, so a client can never run its count ahead of the legs it was
    actually sent."""

    reports: int = 0
    joining: bool = False
    caught_up: int | None = None
    owed: int = 0


@dataclass(slots=True)
class MuncherSync:
    """One client's copy of a Muncher (I §7.3, I §9.5.2, II §16.2). Its create is never reported, so
    ``reports`` counts completed legs from the dispatches only: its n-th report is about leg n.

    A client is *joining* when its copy is not in step: it was introduced after the Muncher was
    made, or its copy has not yet reported a leg another client's report has acted on.
    It is sent no live ``'K'`` or ``'E'``-``'C'`` (a latch or dispatch would replace the leg it is
    still flying, unreported; to a client without the create a ``'K'`` is a stray Muncher) until its
    own copy has reported. ``sent_leg`` is the leg last sent to it as a copy; ``owed`` is how many
    reports it can still make, one per dispatch sent (a report with none owed is out of protocol
    and ignored)."""

    reports: int = 0
    joining: bool = False
    sent_leg: int = 0
    owed: int = 0


@dataclass(slots=True)
class ClientView:
    """One player's client: when it started (the id-reuse guard, I §6.2, II §8) and what it
    holds."""

    player_id: int
    joined_at: float
    held: set[int] = field(default_factory=set)
    #: Guppy id -> this client's leg count, for each guppy it holds (I §7.3).
    legs: dict[int, LegCount] = field(default_factory=dict)
    #: Muncher id -> this client's copy of it, for each Muncher it holds (I §7.3).
    munchers: dict[int, MuncherSync] = field(default_factory=dict)
    #: City-wide state (not entities) -> the value this client was last sent: the territory grid
    #: now, the weather in M9 (I §7.3: "once, plus again on every change").
    told: dict[str, Hashable] = field(default_factory=dict)

    def hold(self, entity_id: int) -> None:
        """The client has been told this entity exists."""
        self.held.add(entity_id)

    def forget(self, entity_id: int) -> None:
        """The client no longer holds it (it was deleted)."""
        self.held.discard(entity_id)
        self.legs.pop(entity_id, None)
        self.munchers.pop(entity_id, None)

    def was_told(self, key: str, value: Hashable) -> bool:
        """Whether the client's copy of city-wide state ``key`` is already at ``value``."""
        return key in self.told and self.told[key] == value

    def tell(self, key: str, value: Hashable) -> None:
        """The client has been sent city-wide state ``key`` at ``value``."""
        self.told[key] = value

    def holds(self, entity_id: int) -> bool:
        return entity_id in self.held
