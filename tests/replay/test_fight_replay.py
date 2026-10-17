"""Does /RESUME's fight test (D4) see the fights in every capture? (II §13 item 3)

The claim checked, over the fixtures we have: a pod is under attack
for ``BATTLE_WINDOW`` seconds after damage is delivered to it (``'I'``) or an enemy shot or grenade
is fired within ``FIGHT_RANGE`` of it. Ground truth is power: a drop of more than 150 below the
pod's trend while it is on the ground or on a low hop is damage (jump costs are 57-73, a Phalanx hit
on a hopping pod ~360; high jump-pad flights are skipped because crashes there look the same). The
server does NOT use that inference, since it confuses jumps and crashes with hits; it is only good
enough to find where the real fights were. Every such drop must fall inside an attack window built
from the log's own messages, or the rule missed a fight.

Each fixture is one client's connection: its own ``'b'``, ``'c'`` go through a ``City`` as the
session would pass them, and what the server sent it (``'I'``, relayed ``'c'`` and ``'E'``-``'G'``)
is fed to the same marking functions the city uses for it. A fixture with no server side (the
July logs, and every one before the server's side was captured) has neither its pod id nor the
damage it was sent, so it cannot be replayed and is skipped. A drop with only the pod's own shots
around is self-inflicted (a hopping pod into its own Phalanx fire) and is not a miss. Teams are not
reconstructed, so in a same-team two-player run this over-marks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fixtures.captures import capture_names, load_capture
from fixtures.cities import Npc
from fixtures.lobbies import city
from opencs import policy
from opencs.messages import DecodeError, client, decode_client, decode_server, server
from opencs.wire.frame import FrameReader
from opencs.world.city import City, JoinResult
from opencs.world.hits import Hits
from opencs.world.player import Player

HURT, HIGH_Y = 150, 50

#: Damage no message the server saw can explain: not a failure of the rule, which can only use what
#: the server is told. Keyed by fixture and second into it; each needs a reason. A NEW miss fails.
UNEXPLAINED: dict[tuple[str, int], str] = {
    ("2026-10-16-113841_002-play-p1.txt", 216): (
        "-235 at the start of a hop (y 16 -> 23), the pod's first move after 66 s frozen at its "
        "respawn point (the player: 'respawned in a wall or something, couldn't move or jump'); "
        "no shot within range, nothing sent; likely collision, not verified"
    ),
    ("2026-10-16-113841_002-play-p1.txt", 275): (
        "-245 on a hop (y 15 -> 26) that fell back to y=8 a second later, 50 ft from where it "
        "started; no shot within range, nothing sent. Same run and shape as the one at 216 s"
    ),
}


@dataclass
class Result:
    name: str
    damaging: int = 0
    missed: list[str] = None  # type: ignore[assignment]
    self_hurt: int = 0
    unexplained: int = 0
    seconds: float = 0.0

    def __post_init__(self) -> None:
        self.missed = []


def self_id(name: str) -> int | None:
    """The pod id the server gave this client in its ``'B'``; None for a log with no server side."""
    reader = FrameReader()
    for f in load_capture(name):
        if f.direction != "S":
            continue
        for frame in reader.feed(f.raw):
            try:
                msg, _ = decode_server(frame.payload)
            except DecodeError:
                continue
            if isinstance(msg, server.Session):
                return msg.self_id
    return None


class Replayer:
    """One fixture's pod in a city, fed what its client sent and what the server sent it."""

    def __init__(self, name: str, me: int) -> None:
        self.me = me
        self.city = City(city("A"))
        for pid in range(me + 1):  # the pod's own id, with enemies beside it that never report
            joined = self.city.join(f"P{pid}", 0 if pid == me else 1, 0.0)
            assert isinstance(joined, JoinResult) and joined.player_id == pid
        pod = self.city.player(me)
        assert pod is not None
        self.pod = pod
        self.result = Result(name)
        self.power: list[tuple[float, int, int, int]] = []
        self.own_shots: list[float] = []

    def from_client(self, msg: client.ClientMessage, t: float) -> None:
        if isinstance(msg, client.PodState):
            self.city.client_message(self.me, msg, t)
            self.power.append((t, msg.power, msg.state, msg.pos.y))
            judge(self.result, self.pod, self.power, self.own_shots, t)
        elif isinstance(msg, client.Shot):
            if msg.shooter != self.me and not self.city.is_known_npc(msg.shooter):
                self.city.spawn(Npc(msg.shooter), t)  # the NPC the client simulates; the live
                # city has spawned it, and only a known NPC's shot is everyone's enemy
            self.city.client_message(self.me, msg, t)
            if msg.shooter == self.me:
                self.own_shots.append(t)

    def from_server(self, msg: server.ServerMessage, t: float) -> None:
        if isinstance(msg, server.Damage):
            self.pod.attacks.mark(t, "DAMAGE")  # what Hits.hit does when it delivers an 'I'
        elif isinstance(msg, server.Shot):
            self.city.hits.note_fire(msg.shooter, msg.pos, t, "ENEMY FIRE")  # relayed to it
        elif isinstance(msg, server.GrenadeInFlight):
            self.city.hits.note_fire(-1, msg.pos, t, "AN ENEMY GRENADE")


def replay(name: str) -> Result | None:
    """Replay one fixture; None if it cannot be (no server side, or an id past a team's seats)."""
    me = self_id(name)
    if me is None or me >= policy.MAX_PLAYERS_PER_TEAM:
        return None
    run = Replayer(name, me)
    rx, tx = FrameReader(), FrameReader()
    frames = load_capture(name)
    for f in frames:
        for frame in (rx if f.direction == "C" else tx).feed(f.raw):
            try:
                if f.direction == "C":
                    run.from_client(decode_client(frame.payload), f.t)
                else:
                    run.from_server(decode_server(frame.payload)[0], f.t)
            except DecodeError:
                continue
    run.result.seconds = frames[-1].t - frames[0].t
    return run.result


def judge(
    result: Result,
    pod: Player,
    power: list[tuple[float, int, int, int]],
    own_shots: list[float],
    t2: float,
) -> None:
    """Is the report just added a damaging moment, and if so was the pod marked as attacked?"""
    if len(power) < 3:
        return
    (t0, p0, _, _), (t1, p1, s1, _), (_, p2, s2, y2) = power[-3], power[-2], power[-1]
    if not t0 < t1 < t2 or abs(p1 - p0) > 500:
        return
    if (s2 != 0 or s1 != 0) and y2 >= HIGH_Y:
        return
    expect = min(3000, p1 + (p1 - p0) / (t1 - t0) * (t2 - t1))
    if expect - p2 <= HURT:
        return
    result.damaging += 1
    if pod.attacks.since(t2 - policy.BATTLE_WINDOW):
        return
    if any(t2 - policy.BATTLE_WINDOW <= s <= t2 for s in own_shots):
        result.self_hurt += 1  # only its own shots around: not a fight (a Phalanx hop)
    elif (result.name, int(t2)) in UNEXPLAINED:
        result.unexplained += 1
    else:
        result.missed.append(f"{result.name} at {t2:.1f}s: power {p1} -> {p2}")


RESULTS: dict[str, Result] = {}
for _name in capture_names():
    _r = replay(_name)
    if _r is not None:
        RESULTS[_name] = _r


def test_the_corpus_has_fights_to_find() -> None:
    assert len(RESULTS) >= 10
    assert sum(r.damaging for r in RESULTS.values()) >= 5


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_every_damaging_moment_is_inside_a_fight_window(name: str) -> None:
    assert RESULTS[name].missed == []


def test_a_rule_that_marks_nothing_misses_the_fights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Hits, "note_fire", lambda self, *a, **k: None)
    missed = [m for n in RESULTS if (r := replay(n)) is not None for m in r.missed]
    assert missed, "no run noticed that shots and grenades no longer mark a pod"
