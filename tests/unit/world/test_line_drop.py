"""A line drop in the city: the record it leaves, and the death it is when the pod was in a fight
(II §13 items 1-4, D1-D9)."""

from __future__ import annotations

import pytest

from fixtures.delivery import to
from fixtures.drops import at, drop, game, player
from fixtures.lobbies import city
from fixtures.pods import report
from opencs.messages import WirePos, client, server
from opencs.world.city import City, JoinResult
from opencs.world.orders import Strike
from opencs.world.resume import ResumeRecord

SPOT = WirePos.from_world(2000, 20, 3000)


# --- the record -------------------------------------------------------------------------------


def test_a_quiet_drop_only_deletes_the_pod_and_leaves_an_eligible_record() -> None:
    c, (a, b) = game([0, 1])
    out, record = drop(c, a, 30.0)
    assert out == [(b, server.Delete(a))]
    assert record is not None and record.eligible and record.reason == ""
    assert c.player(a) is None


def test_the_record_holds_what_a_resume_restores() -> None:
    c, (a, _) = game([2, 1])
    c.client_message(a, client.Status(1670, 5, 957), 10.0)
    c.client_message(a, client.TowerOrder(WirePos.from_world(300, 20, 4500), 1, 0), 11.0)
    p = player(c, a)
    p.loadout.modules.extend([12, 31, 3, 24])  # levels 2, 4, 1, 3
    _, record = drop(c, a, 30.0)
    assert record == ResumeRecord(
        identity="acct:P0",
        callsign="P0",
        city="A",
        team=2,
        score=1670,
        multiplier=3,  # 5 less the MegaTower's 2
        bay_points=957,
        modules=(31, 24, 12, 3),  # highest level first
        dropped_at=30.0,
        eligible=True,
    )


def test_the_records_bay_points_are_the_saved_ones_not_the_last_reports() -> None:
    c, (a,) = game([0])
    c.client_message(a, client.Status(10, 3, 996), 10.0)
    p = player(c, a)
    # The points-upgrade guard spent them after that report (II §11.3).
    c.client_message(a, client.Bays((2, 1, 1, 1, 0, 0, 0, 0, 0), 0), 11.0)
    assert p.progress.record.bay_points == 0 and p.score.bay_points == 996
    _, record = drop(c, a, 30.0)
    assert record is not None and record.bay_points == 0


def test_a_player_nothing_is_kept_for_leaves_no_record() -> None:
    c, (a, b) = game([0, 1], identities=False)
    out, record = drop(c, a, 30.0)
    assert record is None and out == [(b, server.Delete(a))]


def test_an_unknown_player_drops_nothing() -> None:
    c, _ = game([0])
    assert c.line_drop(99, 30.0).events == [] and c.line_drop(99, 30.0).record is None


# --- the fight test ---------------------------------------------------------------------------


def enemy_shot(c: City, shooter: int, now: float, x: int = 2000) -> None:
    c.client_message(shooter, client.Shot(shooter, WirePos.from_world(x, 20, 3000), 0, 0, 1), now)


@pytest.mark.parametrize("signal", ["hit report", "damage", "shot", "grenade", "strike"])
def test_each_attack_signal_within_20_seconds_makes_the_drop_a_death(signal: str) -> None:
    c, (a, b) = game([0, 1])
    if signal == "hit report":
        c.client_message(b, client.HitReport(a, 99), 20.0)  # a class no 'I' can carry
    elif signal == "damage":
        c.client_message(b, client.HitReport(a, 10), 20.0)
    elif signal == "shot":
        enemy_shot(c, b, 20.0)
    elif signal == "grenade":
        c.client_message(b, client.Grenade(SPOT, 0, 0), 20.0)
    else:
        c.orders.strikes.append(Strike(20.0, 1, SPOT))
    out, record = drop(c, a, 40.0)
    assert record is not None and not record.eligible and record.reason
    assert any(isinstance(m, server.Destroy) for _, m in out)


@pytest.mark.parametrize(("age", "fight"), [(20.0, True), (20.01, False)])
def test_the_window_is_20_seconds_inclusive_for_a_shot(age: float, fight: bool) -> None:
    c, (a, b) = game([0, 1])
    enemy_shot(c, b, 40.0 - age)
    _, record = drop(c, a, 40.0)
    assert record is not None and (not record.eligible) is fight


@pytest.mark.parametrize(("age", "fight"), [(20.0, True), (20.01, False)])
def test_the_window_is_20_seconds_inclusive_for_a_strike(age: float, fight: bool) -> None:
    c, (a, _) = game([0, 1])
    c.orders.strikes.append(Strike(40.0 - age, 1, SPOT))
    _, record = drop(c, a, 40.0)
    assert record is not None and (not record.eligible) is fight


def test_an_attack_21_seconds_before_the_drop_is_not_a_fight() -> None:
    c, (a, b) = game([0, 1])
    enemy_shot(c, b, 19.0)
    c.orders.strikes.append(Strike(19.0, 1, SPOT))
    _, record = drop(c, a, 40.0)
    assert record is not None and record.eligible


def test_fire_out_of_range_or_from_a_teammate_or_a_friendly_strike_is_not_a_fight() -> None:
    c, (a, mate, foe) = game([0, 0, 1])
    enemy_shot(c, foe, 39.0, x=2000 + 601)
    enemy_shot(c, mate, 39.0, x=2000)
    c.orders.strikes.append(Strike(39.0, 0, SPOT))  # the pod's own team's guppy
    c.orders.strikes.append(Strike(39.0, 1, WirePos.from_world(2000 + 601, 20, 3000)))
    _, record = drop(c, a, 40.0)
    assert record is not None and record.eligible


def test_the_window_runs_from_when_the_line_went_quiet_not_when_it_was_noticed() -> None:
    c, (a, b) = game([0, 1])
    enemy_shot(c, b, 10.0)
    c2, (a2, b2) = game([0, 1])
    enemy_shot(c2, b2, 10.0)
    # Noticed at 40 s: the shot is 30 s old. The line went quiet at 25 s: 15 s before the drop.
    _, noticed = drop(c, a, 40.0)
    _, quiet = drop(c2, a2, 40.0, fight_at=25.0)
    assert noticed is not None and noticed.eligible
    assert quiet is not None and not quiet.eligible


@pytest.mark.parametrize("signal", ["hit", "shot", "grenade", "strike"])
def test_what_happens_to_the_frozen_pod_after_the_last_byte_is_not_the_players_fight(
    signal: str,
) -> None:
    c, (a, b) = game([0, 1])
    if signal == "hit":
        c.client_message(b, client.HitReport(a, 10), 30.0)
    elif signal == "shot":
        enemy_shot(c, b, 30.0)
    elif signal == "grenade":
        c.client_message(b, client.Grenade(SPOT, 0, 0), 30.0)
    else:
        c.orders.strikes.append(Strike(30.0, 1, SPOT))
    _, record = drop(c, a, 40.0, fight_at=25.0)  # quiet since 25, noticed at 40
    assert record is not None and record.eligible


def test_an_attack_before_the_last_byte_still_counts_when_more_came_after_it() -> None:
    c, (a, b) = game([0, 1])
    enemy_shot(c, b, 25.0)  # exactly at the last byte
    enemy_shot(c, b, 30.0)
    enemy_shot(c, b, 36.0)
    _, record = drop(c, a, 40.0, fight_at=25.0)
    assert record is not None and not record.eligible


def test_a_pod_already_dead_is_not_killed_twice() -> None:
    c, (a, b) = game([0, 1])
    c.client_message(b, client.Shot(b, at(2000).pos, 0, 0, 1), 19.0)
    c.client_message(b, client.HitReport(a, 10), 20.0)
    c.client_message(a, client.Died(), 21.0)
    credit = player(c, b).score.server_credit
    out, record = drop(c, a, 25.0)
    assert record is not None and record.eligible
    assert out == [(b, server.Delete(a))]
    assert player(c, b).score.server_credit == credit


def respawn(c: City, pid: int) -> None:
    """The client's bare 'A', the drop, and the report that lands."""
    c.client_message(pid, client.DropMe(), 24.0)
    c.client_message(pid, report(4), 25.0)
    c.client_message(pid, at(2000), 28.0)


def test_a_pod_that_died_respawned_and_dropped_inside_the_window_is_not_in_a_fight() -> None:
    c, (a, b) = game([0, 1])
    c.client_message(b, client.HitReport(a, 10), 20.0)
    c.client_message(a, client.Died(), 21.0)
    respawn(c, a)  # landed
    credit = player(c, b).score.server_credit
    out, record = drop(c, a, 35.0)
    assert record is not None and record.eligible
    assert out == [(b, server.Delete(a))]  # no second 'J'
    assert player(c, b).score.server_credit == credit


def test_a_strike_before_the_pods_death_is_not_a_fight_for_its_next_life() -> None:
    c, (a, _) = game([0, 1])
    c.orders.strikes.append(Strike(20.0, 1, SPOT))
    c.client_message(a, client.Died(), 21.0)
    respawn(c, a)
    _, record = drop(c, a, 35.0)
    assert record is not None and record.eligible


def test_an_attack_after_the_respawn_is_still_a_fight() -> None:
    c, (a, b) = game([0, 1])
    c.client_message(a, client.Died(), 21.0)
    respawn(c, a)
    enemy_shot(c, b, 30.0)
    _, record = drop(c, a, 35.0)
    assert record is not None and not record.eligible


def test_a_pod_that_never_reported_is_eligible() -> None:
    c = City(city("A"))
    r = c.join("P0", 0, 0.0, identity="acct:P0")
    assert isinstance(r, JoinResult)
    _, record = drop(c, r.player_id, 5.0)
    assert record is not None and record.eligible


# --- the death at the drop --------------------------------------------------------------------


def hit(c: City, shooter: int, victim: int, at_: float) -> None:
    c.client_message(shooter, client.Shot(shooter, at(2000).pos, 0, 0, 1), at_ - 1.0)
    c.client_message(shooter, client.HitReport(victim, 0), at_)


def test_a_death_at_the_drop_pays_notifies_kills_zeroes_then_deletes_in_that_order() -> None:
    c, (a, hitter, other, mate) = game([0, 1, 2, 0])
    c.client_message(hitter, client.Status(0, 4, 0), 2.0)
    p = player(c, a)
    p.loadout.modules.append(31)
    hit(c, hitter, a, 30.0)
    out, record = drop(c, a, 40.0)  # alive since t=1: a bounty of 390
    # Everyone else is sent the same sequence; the hitter's kill notice comes first.
    for pid in (other, mate):  # 'J', the bounty zero ('E'-'B'), then the delete
        assert [type(m) for m in to(out, pid)] == [server.Destroy, server.SetBounty, server.Delete]
    kinds = [type(m) for m in to(out, hitter)]
    assert kinds == [server.KillNotice, server.Destroy, server.SetBounty, server.Delete]
    assert to(out, hitter)[2] == server.SetBounty(a, 0)
    assert player(c, hitter).score.server_credit == 390 * 4
    assert record is not None and record.modules == () and not record.eligible
    assert a not in [pid for pid, _ in out]  # nothing is sent to the dropped pod


def test_a_silent_lines_death_is_settled_as_of_the_last_byte() -> None:
    c, (a, hitter, late) = game([0, 1, 2])
    hit(c, hitter, a, 10.0)  # inside 20 s of 25 s, outside it of 40 s
    hit(c, late, a, 30.0)  # after the last byte: the pod was already gone
    out, record = drop(c, a, 40.0, fight_at=25.0)  # quiet since 25, noticed at 40
    assert record is not None and not record.eligible
    assert isinstance(to(out, hitter)[0], server.KillNotice)
    assert not any(isinstance(m, server.KillNotice) for m in to(out, late))
    assert player(c, late).score.server_credit == 0
    # Valued at 25 s: alive since t=1, a bounty of 240, not the 390 of the noticing at 40.
    assert player(c, hitter).score.server_credit == 240 * player(c, hitter).score.multiplier


def test_the_death_is_the_clients_own_reset_in_the_mirror_and_the_record() -> None:
    c, (a, hitter) = game([0, 1])
    c.client_message(a, client.Bays((1, 1, 1, 1, 0, 0, 0, 0, 0), 2), 5.0)
    p = player(c, a)
    hit(c, hitter, a, 30.0)
    drop(c, a, 40.0)
    assert p.progress.record.marker == 1  # the battle marker down one, and saved
    changed = {n.identity: n.record for n in c.take_progress()}
    assert changed["acct:P0"].marker == 1


@pytest.mark.parametrize(("lowest", "earned"), [(2, True), (3, False)])
def test_the_kill_notice_is_earned_when_the_victims_best_bay_is_within_one(
    lowest: int, earned: bool
) -> None:
    c, (a, hitter) = game([0, 1])  # the victim's highest bay is 1
    player(c, hitter).score.bays = (lowest,) * 9
    hit(c, hitter, a, 30.0)
    out, _ = drop(c, a, 40.0)
    assert to(out, hitter)[0] == server.KillNotice(earned)
