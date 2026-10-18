"""Hit attribution on real captured streams (II §9.3, I §9.1).

The ground truth comes from the captured runs. In ``hit-dedup``, p1 (id 0) fired 11 own shots and
reported 11 hits on p2's pod; p2 (id 1) fired 18 and reported 18 hits on p1's pod plus 9 on a mine
(id 38) that the Sentinel caused, which must credit nobody. In the contested run the player fired 15
shots, a Sentinel 46, and exactly 15 hit reports followed, all of them the player's: 6 had a
Sentinel shot in the window as well. These are the streams, in order, with the time each frame
arrived."""

from __future__ import annotations

from fixtures.captures import client_messages
from fixtures.lobbies import city
from opencs.messages import client
from opencs.world.city import City
from opencs.world.combat import HitSet, ShotLog
from opencs.world.seating import JoinResult

HIT_DEDUP = {
    0: "2026-09-03-123853_001-hit-dedup-p1.txt",
    1: "2026-09-03-123853_002-hit-dedup-p2.txt",
}
CONTESTED = "2026-09-15-102453_001-bounty-payout-p1.txt"


def credited_per_target(pod: int, name: str) -> dict[int, int]:
    """Replay one connection's shots and hit reports through its own ShotLog: how many reports on
    each target were credited to its player."""
    log = ShotLog(pod)
    credited: dict[int, int] = {}
    for t, msg in client_messages(name):
        if isinstance(msg, client.Shot):
            log.record(t, msg.shooter)
        elif isinstance(msg, client.HitReport):
            result = log.attribute(t)
            credited.setdefault(msg.target, 0)
            credited[msg.target] += result.credited
    return credited


def reports_per_target(name: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for _, msg in client_messages(name):
        if isinstance(msg, client.HitReport):
            counts[msg.target] = counts.get(msg.target, 0) + 1
    return counts


def test_p1_is_credited_for_11_hits_on_the_other_pod() -> None:
    assert reports_per_target(HIT_DEDUP[0]) == {1: 11}
    assert credited_per_target(0, HIT_DEDUP[0]) == {1: 11}


def test_p2_is_credited_for_its_18_pod_hits_and_none_of_its_9_mine_hits() -> None:
    assert reports_per_target(HIT_DEDUP[1]) == {0: 18, 38: 9}
    assert credited_per_target(1, HIT_DEDUP[1]) == {0: 18, 38: 0}


def test_the_contested_run_credits_all_15_reports() -> None:
    reports = reports_per_target(CONTESTED)
    assert sum(reports.values()) == 15
    assert sum(credited_per_target(0, CONTESTED).values()) == 15


def test_the_contested_run_really_has_a_competing_npc_shot_for_some_hits() -> None:
    """Guard on the fixture: without contested hits this run would not tell the rules apart."""
    log = ShotLog(0)
    contested = 0
    for t, msg in client_messages(CONTESTED):
        if isinstance(msg, client.Shot):
            log.record(t, msg.shooter)
        elif isinstance(msg, client.HitReport):
            window = [s.shooter for s in log._shots if abs(t - 1.0 - s.t) <= 1.0]
            contested += any(w == 0 for w in window) and any(w != 0 for w in window)
            log.attribute(t)
    assert contested >= 6


def test_the_two_clients_through_one_city_credit_the_same_hits() -> None:
    """The same streams through ``City.client_message``: the hit sets, not just the log."""
    c = City(city("A"))
    for callsign, team in (("P1", 0), ("P2", 1)):
        assert isinstance(c.join(callsign, team, 0.0), JoinResult)

    class Spy(HitSet):
        def __init__(self) -> None:
            super().__init__()
            self.credits: list[int] = []

        def record(self, payee: int, now: float) -> None:
            self.credits.append(payee)
            super().record(payee, now)

    spies = {pid: Spy() for pid in (0, 1)}
    for pid, spy in spies.items():
        player = c.player(pid)
        assert player is not None
        player.hits = spy
    merged = sorted(
        (t, pid, msg) for pid, name in HIT_DEDUP.items() for t, msg in client_messages(name)
    )
    # The two logs' clocks start together to within a second; attribution reads only one
    # connection's own times, so the offset between them cannot change which shot a report uses.
    for t, pid, msg in merged:
        c.client_message(pid, msg, t)
    # p1 hit p2: credits recorded in p2's hit set; and p2 hit p1.
    assert len(spies[1].credits) == 11 and set(spies[1].credits) == {0}
    assert len(spies[0].credits) == 18 and set(spies[0].credits) == {1}
