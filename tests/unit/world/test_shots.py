"""The city's handling of a client's ``'c'``: the shot log, shooter validation, the relay
(I §8.25, II §9.3, I §11.4, II §9.2)."""

from __future__ import annotations

import pytest

from fixtures.cities import Npc, seat
from fixtures.delivery import deliveries
from fixtures.pods import report
from opencs.messages import client, server


def shot(shooter: int, kind: int = 1) -> client.Shot:
    return client.Shot(shooter, report(0, x_off=207, y=212, z_off=235).pos, 3196, 0, kind)


def test_a_shot_is_logged_on_the_senders_connection_only() -> None:
    c, (a, b, _) = seat([0, 1, 1])
    c.client_message(a, shot(a), 1.0)
    pa, pb = c.player(a), c.player(b)
    assert pa is not None and pb is not None
    assert len(pa.shots) == 1 and len(pb.shots) == 0


def test_an_unsafe_shot_is_not_relayed_but_is_still_logged() -> None:
    # II §9.3: declining the relay says nothing about the client simulating it.
    c, (a, *_) = seat([0, 1, 1])
    assert c.client_message(a, shot(a, kind=0), 1.0) == []
    assert c.unsafe_shots == 1
    pa = c.player(a)
    assert pa is not None and len(pa.shots) == 1


def test_a_known_npcs_shot_is_logged_and_relayed_without_complaint() -> None:
    c, (a, b, d) = seat([0, 1, 1])
    c.spawn(Npc(4100), 0.0)
    out = deliveries(c, c.client_message(a, shot(4100, kind=2), 1.0))
    assert [pid for pid, _ in out] == [b, d]
    assert c.shooter_mismatches == 0
    pa = c.player(a)
    assert pa is not None and pa.shots._shots[0].shooter == 4100


@pytest.mark.parametrize("claimed", [1, 77, 4100])
def test_a_shooter_that_is_neither_the_senders_pod_nor_a_known_npc_is_counted_and_still_relayed(
    claimed: int,
) -> None:
    # 1 is another player's pod, 77 and 4100 are unknown ids.
    c, (a, b, d) = seat([0, 1, 1])
    out = deliveries(c, c.client_message(a, shot(claimed), 1.0))
    assert [pid for pid, _ in out] == [b, d]
    assert c.shooter_mismatches == 1
    pa = c.player(a)
    assert pa is not None and pa.shots._shots[0].shooter == claimed  # logged as claimed


def test_the_senders_own_shot_is_not_a_mismatch_and_relays_the_fields_verbatim() -> None:
    c, (a, *_) = seat([0, 1, 1])
    fired = shot(a, kind=12)
    out = deliveries(c, c.client_message(a, fired, 1.0))
    (_, msg) = out[0]
    assert isinstance(msg, server.Shot)
    assert (msg.id, msg.pos, msg.orient, msg.pitch, msg.kind) == (a, fired.pos, 3196, 0, 12)
    assert c.shooter_mismatches == 0


def test_a_relayed_shooter_id_counts_as_possibly_held_by_each_recipient_and_not_by_the_sender() -> (
    None
):
    # I §6.2: the recipient's client may have linked a node under the shot's shooter id.
    c, (a, b, d) = seat([0, 1, 1])
    c.client_message(a, shot(4100, kind=2), 1.0)
    views = {pid: c.player(pid).view for pid in (a, b, d)}  # type: ignore[union-attr]
    assert views[b].has_had(4100) and views[d].has_had(4100)
    assert not views[a].has_had(4100)


def test_a_shot_that_is_not_relayed_leaves_no_trace_in_the_views() -> None:
    c, (a, b, _) = seat([0, 1, 1])
    c.client_message(a, shot(4100, kind=0), 1.0)
    assert not c.player(b).view.has_had(4100)  # type: ignore[union-attr]


def test_a_shooter_id_the_server_never_allocates_is_not_remembered() -> None:
    c, (a, b, _) = seat([0, 1, 1])
    c.client_message(a, shot(65000), 1.0)
    assert not c.player(b).view.has_had(65000)  # type: ignore[union-attr]
