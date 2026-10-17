"""Every captured run with module traffic, replayed through the installed-module ledger.

I §9.3.4, II §13.

The claim: a pod's installed modules are reconstructible from messages the server already handles,
and the client's own weight in every ``'b'`` confirms it. Each fixture is fed in log order:

* server ``'P'`` frames give object id -> module; ``'Q'`` sub 0 grants install that object's module
  and ``'Q'`` sub 5 installs a module directly;
* client ``'Q'`` sub 1 (eject) and sub 4 (used up) remove one;
* the client's bare ``'J'`` is a death and clears all;
* every client ``'b'`` reports a weight, which must agree with the ledger past the grace.

Runs whose capture holds only the client's side (July 2026) cannot say which module a grant was
for, so they are not replayed. The exception is ``module-rack``, whose installs are ``'Q'`` sub 5
frames the older log dumped on INFO lines (``make_fixture.py --info-frames``): it holds three
installs the client never took. Three broken ledgers must each disagree somewhere, so the check is
known to have teeth: one that ignores removals, one that ignores deaths and one that trusts every
install (which the module-rack run catches on real data).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from fixtures.captures import capture_names, load_capture
from opencs.messages import DecodeError, client, decode_client, server
from opencs.messages.base import decode_server
from opencs.wire.frame import FrameReader
from opencs.world.loadout import Loadout

MEGA_TOWER = 21  # weight 8: large enough to show in any report


@dataclass
class Result:
    installs: int = 0
    removals: int = 0
    deaths: int = 0
    reports: int = 0
    failures: list[str] = field(default_factory=list)
    never_landed: int = 0


class Trusting(Loadout):
    """The 'trust every install' break: an install is confirmed at once."""

    def install(self, module: int) -> None:
        self.modules.append(module)


class Replayer:
    """One fixture's frames fed to a ledger, optionally a broken one."""

    def __init__(self, broken: str | None, phantom: int | None) -> None:
        self.broken = broken
        self.phantom = phantom
        self.ledger: Loadout = Trusting() if broken == "pending" else Loadout()
        self.objects: dict[int, int] = {}
        self.out = Result()

    def server_frame(self, payload: bytes) -> None:
        try:
            msg, _tag = decode_server(payload)
        except DecodeError:
            return  # a frame our messages do not model (an old build's 'B'): not about modules
        if isinstance(msg, server.SpawnModule):
            self.objects[msg.id] = msg.module
        elif isinstance(msg, server.GrantPickup) and msg.object in self.objects:
            self.install(self.objects[msg.object])
        elif isinstance(msg, server.InstallModule):
            self.install(msg.module)

    def install(self, module: int) -> None:
        self.out.installs += 1
        self.ledger.install(module)

    def client_frame(self, t: float, payload: bytes) -> None:
        try:
            msg = decode_client(payload)
        except DecodeError:
            return
        if isinstance(msg, client.EjectRequest | client.ModuleUsedUp):
            self.out.removals += 1
            if self.broken != "remove":
                self.ledger.remove(msg.module)
        elif isinstance(msg, client.Died):
            self.out.deaths += 1
            if self.broken != "clear":
                self.ledger.clear()
        elif isinstance(msg, client.PodState):
            if self.phantom is not None and self.out.reports == 0:
                self.ledger.install(self.phantom)  # a grant the client's bays could not take
            self.out.reports += 1
            bad = self.ledger.report(msg.weight)
            if bad:
                self.out.failures.append(f"t={t}: {bad}")


def replay(name: str, broken: str | None = None, phantom: int | None = None) -> Result:
    r = Replayer(broken, phantom)
    s_reader, c_reader = FrameReader(), FrameReader()
    for f in load_capture(name):
        if f.direction == "S":
            for frame in s_reader.feed(f.raw):
                r.server_frame(frame.payload)
        else:
            for frame in c_reader.feed(f.raw):
                r.client_frame(f.t, frame.payload)
    r.out.never_landed = len(r.ledger.failed)
    return r.out


def module_fixtures() -> list[str]:
    return [n for n in capture_names() if replay(n).installs]


FIXTURES = module_fixtures()


def test_there_are_runs_with_module_traffic_to_replay() -> None:
    assert len(FIXTURES) >= 16
    assert sum(replay(n).reports for n in FIXTURES) >= 1500
    assert sum(replay(n).removals for n in FIXTURES) >= 10


@pytest.mark.parametrize("name", FIXTURES)
def test_the_ledger_agrees_with_the_clients_weight_in_every_run(name: str) -> None:
    result = replay(name)
    assert result.failures == []


def test_the_corpus_holds_deaths() -> None:
    assert sum(replay(n).deaths for n in FIXTURES) >= 3


@pytest.mark.parametrize("broken", ["remove", "clear"])
def test_a_ledger_that_ignores_removals_or_deaths_disagrees(broken: str) -> None:
    failing = [n for n in FIXTURES if replay(n, broken).failures]
    assert failing, f"no run noticed a ledger that ignores {broken}"


def test_a_ledger_that_ignores_deaths_fails_only_on_runs_that_died() -> None:
    failing = {n for n in FIXTURES if replay(n, "clear").failures}
    assert failing
    assert failing <= {n for n in FIXTURES if replay(n).deaths}


MODULE_RACK = "2026-07-29-015155_001-module-rack.txt"


def test_the_installs_a_real_client_never_took_are_dropped_as_never_landed() -> None:
    # Three 'Q' sub 5 installs (11, 12, 13) that left the client's weight unchanged (I §9.3.4, II
    # §13).
    result = replay(MODULE_RACK)
    assert result.installs == 3 and result.never_landed == 3
    assert result.failures == []


def test_a_ledger_that_trusts_every_install_disagrees_with_the_real_client() -> None:
    assert replay(MODULE_RACK, "pending").failures


def test_an_injected_phantom_install_is_dropped_by_the_ledger_but_not_by_one_that_trusts() -> None:
    # A supplement to the module-rack run: a phantom install injected into another real stream,
    # whose weight never shows it. The outcome is decided by the injection, not by the client.
    name = "2026-10-16-113841_002-play-p1.txt"
    real = replay(name, phantom=MEGA_TOWER)
    assert real.failures == [] and real.never_landed == 1
    trusting = replay(name, "pending", phantom=MEGA_TOWER)
    assert trusting.failures
