"""Shots, hits and who gets the credit (I §9.1, II §9.3, II §9).

A client reports a hit with ``'I'`` and no author: the connection it arrived on is the author
(I §9.1.2). The shot that caused it is the one that connection reported with ``'c'`` about a
second earlier, so attribution is a correlation over *that connection's own* shot log. Two things
stay apart (II §9.1): damage is an effect, relayed whoever is credited;
credit is who is paid for the kill. Nothing here knows about damage, only credit.
"""

from __future__ import annotations

from dataclasses import dataclass

from opencs import policy
from opencs.messages import limits, server

#: The earliest a shot can be and still match a report made now, in seconds back (II §9.3:
#: ``now - 1.0 +- 1.0``).
_OLDEST_MATCH = policy.SHOT_FLIGHT_TIME + policy.SHOT_MATCH_TOLERANCE


@dataclass(frozen=True, slots=True)
class LoggedShot:
    """A ``'c'`` this connection reported: when, and the shooter id it named."""

    t: float
    shooter: int


@dataclass(frozen=True, slots=True)
class Attribution:
    """The outcome of one hit report. ``consumed`` is the shot it used up (None if there was
    none); ``credited`` is True only when that shot was the reporter's own pod's."""

    credited: bool
    consumed: LoggedShot | None


class ShotLog:
    """One connection's shots (II §9.3: attribution can only credit that
    connection's player or nobody). NPC-authored shots are logged as well, and whether or not the
    relay was declined (property 3): they are what stops a hit from being credited to the player."""

    def __init__(self, own_id: int) -> None:
        self.own_id = own_id
        self._shots: list[LoggedShot] = []

    def __len__(self) -> int:
        return len(self._shots)

    def record(self, now: float, shooter: int) -> None:
        """Append ``(now, shooter)``. Shots too old to match any later report are dropped, and the
        log is capped at ``SHOT_LOG_MAX``, oldest first."""
        self._age_out(now)
        self._shots.append(LoggedShot(now, shooter))
        del self._shots[: -policy.SHOT_LOG_MAX]

    def attribute(self, now: float) -> Attribution:
        """Use up one shot for a hit report made ``now`` (II §9.3).

        Candidates are the unconsumed shots within ``now - 1.0 +- 1.0`` s. None: nobody is
        credited. Any candidate by the reporter's own pod: the one nearest the expected flight time
        is consumed and credited, even when an NPC's shot is nearer. Otherwise the nearest is
        consumed and nobody is credited (the client only simulated it). One shot per report.
        """
        self._age_out(now)
        expected = now - policy.SHOT_FLIGHT_TIME
        candidates = [s for s in self._shots if abs(expected - s.t) <= policy.SHOT_MATCH_TOLERANCE]
        if not candidates:
            return Attribution(False, None)

        def nearest(shots: list[LoggedShot]) -> LoggedShot:
            # Nearest the expected flight time; the later shot on an exact tie.
            return min(shots, key=lambda s: (abs(expected - s.t), -s.t))

        own = [s for s in candidates if s.shooter == self.own_id]
        chosen = nearest(own or candidates)
        self._shots.remove(chosen)
        return Attribution(bool(own), chosen)

    def _age_out(self, now: float) -> None:
        self._shots = [s for s in self._shots if now - s.t <= _OLDEST_MATCH]


class HitSet:
    """Who hit one victim pod, and when: ``payee -> time of the last attributed hit`` (I §9.2.8, II
    §11.2, II §9.3). A set, so a second hit by the same player only refreshes its time."""

    def __init__(self) -> None:
        self._hits: dict[int, float] = {}

    def record(self, payee: int, now: float) -> None:
        self._hits[payee] = now

    def payees(self, now: float) -> list[int]:
        """The players that hit the pod within ``PAYOUT_WINDOW`` seconds, in id order. A hit
        exactly that old still counts (the manual: "older than" 20 s does not)."""
        return sorted(p for p, t in self._hits.items() if now - t <= policy.PAYOUT_WINDOW)

    def clear(self) -> None:
        self._hits.clear()


def damage_to_victim(reporter_id: int, damage: int) -> server.Damage | None:
    """The ``'I'`` the victim's client is sent for a hit another client reported (I §8.11, II §9.1):
    the damage byte verbatim, the attacker field the reporter's pod (the client ignores it). None if
    the byte's weapon class (``byte % 10``) is past the client's five-entry table, where it would
    read outside it: the caller drops and counts it.
    """
    if damage % 10 > limits.DAMAGE_CLASS_MAX:
        return None
    return server.Damage(reporter_id, damage)
