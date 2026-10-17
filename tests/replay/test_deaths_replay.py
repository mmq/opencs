"""Every captured death, replayed through a City from the client's real report stream.

The post-death drop rule was got wrong three times in earlier implementations, and twice its
hand-written test passed on broken code: it fed an idealized death-then-state-4, while the real
client sends a report after its ``'J'`` that still says state 0, and that report canceled the wait.
So these drive the actual streams, ticking the city at its own rate in between (a stalled resend
would come from a tick), and check the properties of I §9.2.9 and I §7.5 B.2:

* ``'J' <id>`` goes to every client after the client's bare ``'J'``;
* no ``'A'`` for that pod reaches anyone between the death and the client's bare ``'A'`` (or, with
  none, the ``DROP_REQUEST_TIMEOUT`` failsafe), however many state-0 and state-4 reports come in;
* the ``'A'`` is sent the moment the bare ``'A'`` arrives;
* the bounty zero follows the ``'J'``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fixtures.captures import capture_names, client_messages
from fixtures.delivery import deliveries
from fixtures.lobbies import city
from fixtures.pods import report
from opencs import policy
from opencs.messages import ServerMessage, client, server
from opencs.world.city import City, JoinResult

OBSERVER = 1


@dataclass(frozen=True, slots=True)
class Sent:
    t: float
    to: int
    message: ServerMessage


def replay(name: str) -> tuple[list[Sent], list[tuple[float, str]]]:
    """Everything the city sent while one captured client played, and that client's marks:
    ``(t, 'death' | 'drop')`` for each bare ``'J'`` and bare ``'A'``."""
    c = City(city("A"))
    for callsign, team in (("CAPTURED", 0), ("WATCHER", 1)):
        assert isinstance(c.join(callsign, team, 0.0), JoinResult)
    c.client_message(OBSERVER, report(0), 0.0)
    sent: list[Sent] = []
    marks: list[tuple[float, str]] = []
    now = 0.0
    tick = 0.0

    def emit(t: float, events: list) -> None:  # type: ignore[type-arg]
        sent.extend(Sent(t, pid, m) for pid, m in deliveries(c, events))

    for t, msg in client_messages(name):
        while tick + 1.0 / policy.TICK_HZ <= t:
            tick += 1.0 / policy.TICK_HZ
            emit(tick, c.tick(tick))
        now = max(now, t)
        if isinstance(msg, client.Died):
            marks.append((now, "death"))
        elif isinstance(msg, client.DropMe):
            marks.append((now, "drop"))
        emit(now, c.client_message(0, msg, now))
    return sent, marks


def fixtures_with_deaths() -> list[str]:
    return [
        n for n in capture_names() if any(isinstance(m, client.Died) for _, m in client_messages(n))
    ]


DEATH_FIXTURES = fixtures_with_deaths()


def test_there_are_many_real_deaths_to_replay() -> None:
    deaths = sum(isinstance(m, client.Died) for n in DEATH_FIXTURES for _, m in client_messages(n))
    assert len(DEATH_FIXTURES) >= 20 and deaths >= 30


@pytest.mark.parametrize("name", DEATH_FIXTURES)
def test_each_death_is_a_j_to_everyone_then_silence_until_the_bare_a_then_an_a(name: str) -> None:
    sent, marks = replay(name)
    me = 0
    effective = sorted({s.t for s in sent if s.message == server.Destroy(me)})
    deaths = [t for t, kind in marks if kind == "death"]
    assert deaths and effective
    # A client 'J' is answered unless the pod is already dead: the real client sends a second one
    # within a second of the first (A.3b's double death), which must not be answered again.
    repeats = [t for t in deaths if t not in effective]
    for t in repeats:
        previous = max(e for e in effective if e < t)
        assert t - previous < policy.DROP_REQUEST_TIMEOUT
        assert not [m for m in marks if m[1] == "drop" and previous < m[0] < t]
    for died_at in effective:
        assert {s.to for s in sent if s.t == died_at and s.message == server.Destroy(me)} == {
            me,
            OBSERVER,
        }  # the victim's own client too
        ask = next((t for t, kind in marks if kind == "drop" and t > died_at), None)
        later = [e for e in effective if e > died_at]
        window_end = min(
            ask if ask is not None else float("inf"),
            died_at + policy.DROP_REQUEST_TIMEOUT,
            later[0] if later else float("inf"),
        )
        stray = [
            s for s in sent if s.message == server.ReleaseDrop(me) and died_at < s.t < window_end
        ]
        assert stray == [], f"{name}: 'A' sent {stray[0].t - died_at:.2f} s after the death"
        if ask is not None and ask < died_at + policy.DROP_REQUEST_TIMEOUT:
            answered = [s for s in sent if s.message == server.ReleaseDrop(me) and s.t == ask]
            assert {s.to for s in answered} == {me, OBSERVER}, f"{name}: no 'A' for the bare 'A'"


@pytest.mark.parametrize("name", DEATH_FIXTURES)
def test_the_bounty_zero_follows_each_j(name: str) -> None:
    sent, _ = replay(name)
    pushes = [
        s
        for s in sent
        if s.to == OBSERVER
        and isinstance(s.message, server.SetBounty | server.Destroy)
        and s.message.id == 0
    ]
    for i, s in enumerate(pushes):
        if s.message == server.Destroy(0):
            nxt = pushes[i + 1]
            assert nxt.message == server.SetBounty(0, 0), f"{name}: no zero right after the J"
            assert nxt.t == s.t


def test_the_captures_include_a_state_0_report_right_after_the_j() -> None:
    """Guard on the corpus: the lesson in the module docstring only bites if a real stream has the
    client's first report after its ``'J'`` saying state 0; the tests above replay those."""
    found = 0
    for name in DEATH_FIXTURES:
        stream = client_messages(name)
        for i, (_, msg) in enumerate(stream):
            if not isinstance(msg, client.Died):
                continue
            after = next((m for _, m in stream[i + 1 :] if isinstance(m, client.PodState)), None)
            if after is not None and after.state == 0:
                found += 1
    assert found >= 1
