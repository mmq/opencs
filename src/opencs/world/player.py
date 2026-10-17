"""A player in a city: identity, slot, and every per-player record the rules keep (I §6.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from opencs.messages import ServerMessage, server
from opencs.world.combat import HitSet, ShotLog
from opencs.world.grace import Grace
from opencs.world.loadout import Loadout
from opencs.world.pods import Pod, PodPhase
from opencs.world.scoring import Bounty, PlayerScore
from opencs.world.view import ClientView


@dataclass(slots=True)
class Player:
    """A player in a city: identity, slot, what its client holds and its pod's life."""

    id: int
    callsign: str
    team: int
    index: int  # 0-3 within the team; unique per (team, index), I §6.3
    view: ClientView
    pod: Pod = field(init=False)
    #: The shots this player's connection reported, for attributing its hit reports (II §9.3).
    shots: ShotLog = field(init=False)
    #: Who hit this player's pod lately, for the bounty payout at its death (I §9.2).
    hits: HitSet = field(init=False)
    #: The server's mirror of this client's SCORE, multiplier, bays and marker (II §11.1, I §10).
    score: PlayerScore = field(init=False)
    #: This pod's bounty (I §9.2).
    bounty: Bounty = field(init=False)
    #: The spawn grace period as the client keeps it (I §9.1.5); mirrored, rules unchanged.
    grace: Grace = field(init=False)
    #: The modules this pod carries, as far as the server's messages say (I §9.3.4, II §13).
    loadout: Loadout = field(init=False)

    def __post_init__(self) -> None:
        self.pod = Pod(self.id)
        self.shots = ShotLog(self.id)
        self.hits = HitSet()
        self.score = PlayerScore()
        self.bounty = Bounty(self.id)
        self.grace = Grace(self.id, self.team)
        self.loadout = Loadout()

    @property
    def entity_id(self) -> int:
        return self.id

    def creating_messages(self, now: float) -> Sequence[ServerMessage]:
        """``'G'`` at the pod's last position, then ``'A'``: ``'G'`` leaves the new pod in drop
        state and a pod in drop state discards every ``'b'`` (I §7.2 steps 2 and 3). A pod that
        is dead and awaiting its drop is introduced where it last stood without the ``'A'``: every
        other client has it in drop state since the ``'J'``, so the newcomer's copy waits there
        too, and the ``'A'`` that releases the pod for them (its bare ``'A'``, or the timeout)
        reaches the newcomer as a holder (I §7.2)."""
        placed = self.pod.placed
        assert placed is not None, "a pod is introduced only after its first 'b' (I §7.2)"
        introduce = server.Introduce(
            id=self.id,
            callsign=self.callsign.encode("ascii"),
            index=self.index,
            team=self.team,
            pos=placed.pos,
            orient=placed.orient,
            bounty_div10=self.bounty.value(now),
        )
        if self.pod.phase is PodPhase.DEAD:
            return (introduce,)
        return (introduce, server.ReleaseDrop(self.id))
