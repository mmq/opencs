"""One player's pod life: drop, play, death, respawn (I §7.1, I §7.5, II §7, I §7.8).

```
AWAIT_FIRST_FRAME -(b, state 4)-> DROPPING -(b, state != 4)-> IN_PLAY
IN_PLAY -(bare J)-> DEAD -(bare A, or DROP_REQUEST_TIMEOUT)-> DROPPING
```

While a pod reports state 4 (``POD_STATE_DROPPING``) we answer with ``'A' <id>`` on every report and
every tick; ``'A'`` is a no-op once it has left drop state (I §7.5). After a death the client flies
over and its pod reports state 4 *during the fly-over*; answering those would re-drop the pod
mid-animation, so a dead pod answers only the client's bare ``'A'``. The wait after a death is
one state (``DEAD``): the client's fly-over and its pending drop request look the same on the wire.
"""

from __future__ import annotations

from enum import Enum, auto

from opencs import policy
from opencs.messages import client, server
from opencs.world.events import Event, ToAll, ToHolders


class PodPhase(Enum):
    """Where a pod is in its life."""

    AWAIT_FIRST_FRAME = auto()  # the client has not reported yet
    DROPPING = auto()  # reporting state 4: answer with 'A'
    IN_PLAY = auto()  # falling, touching down or on the ground (states 1, 2, 0)
    DEAD = auto()  # after the bare 'J': flying over, waiting for the bare 'A'


class Pod:
    """A player's pod as the server tracks it: its phase and last report (I §8.24)."""

    def __init__(self, pod_id: int) -> None:
        self.id = pod_id
        self.phase = PodPhase.AWAIT_FIRST_FRAME
        #: The pod's last ``'b'`` (position, orientation, state, power, weight, armor).
        self.last: client.PodState | None = None
        self._died_at = 0.0

    @property
    def reported_state(self) -> int | None:
        """The state byte of the last ``'b'``, or None before the first."""
        return None if self.last is None else self.last.state

    # --- the client's messages ---------------------------------------------------------------

    def on_report(self, report: client.PodState, now: float) -> list[Event]:
        """A ``'b'`` from the client. The owner never gets its own ``'b'`` back (I App. B)."""
        self._expire(now)
        self.last = report
        if self.phase is PodPhase.DEAD:
            return []  # the fly-over reports state 4; answering it would re-drop the pod
        if report.state == policy.POD_STATE_DROPPING:
            self.phase = PodPhase.DROPPING
            return [self._release()]
        self.phase = PodPhase.IN_PLAY
        return []

    def on_drop_request(self, now: float) -> list[Event]:
        """The client's bare ``'A'``: the fly-over is done, drop me (I §7.5, S5). Answered at once,
        never refused. A pod that has not died has not asked, so it is ignored."""
        self._expire(now)
        if self.phase not in (PodPhase.DEAD, PodPhase.DROPPING):
            return []
        self.phase = PodPhase.DROPPING
        return [self._release()]

    def on_death(self, now: float) -> list[Event]:
        """The client's bare ``'J'`` (I §9.2.9 step 2): ``'J' <id>`` to every client including
        the victim, which does not finish dying without it. Payout, ``'E'``-``'K'`` and the bounty
        zero are M6's; they run before this and extend this method. A second ``'J'`` before the
        respawn is ignored."""
        self._expire(now)
        if self.phase is PodPhase.DEAD:
            return []
        self.phase = PodPhase.DEAD
        self._died_at = now
        return [Event(server.Destroy(self.id), ToAll())]

    # --- time --------------------------------------------------------------------------------

    def tick(self, now: float) -> list[Event]:
        """Repeat ``'A'`` while the pod still reports state 4 (I §7.1, II §7: 5/s)."""
        self._expire(now)
        if self.phase is PodPhase.DROPPING and self.reported_state == policy.POD_STATE_DROPPING:
            return [self._release()]
        return []

    def _expire(self, now: float) -> None:
        """No bare ``'A'`` within ``DROP_REQUEST_TIMEOUT`` of a death: go back to answering state-4
        reports (I §7.1, II §7 failsafe, a server choice)."""
        if self.phase is PodPhase.DEAD and now - self._died_at >= policy.DROP_REQUEST_TIMEOUT:
            self.phase = PodPhase.DROPPING

    def _release(self) -> Event:
        """``'A' <id>`` to every client holding this pod: its copies elsewhere wait in drop state
        too (I App. B)."""
        return Event(server.ReleaseDrop(self.id), ToHolders(self.id))
