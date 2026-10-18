"""Reloading city configuration (M12, DESIGN §6): new cities appear, changed ones take effect when
empty, removed ones close and go once empty, and an invalid file changes nothing (II §17:
a running game is never changed under its players)."""

from __future__ import annotations

import asyncio
import os
import random
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.lobbies import city
from fixtures.loopback import TIMEOUT, LogWatch, TickGate, local_config, start
from opencs.app import ConfigFile, build_services
from opencs.cities import CityManager
from opencs.clock import ManualClock
from opencs.config import AppConfig, CityConfig, load_config
from opencs.lobby.lobby import Denied, Placement
from opencs.session.phases import Services
from opencs.world.seating import JoinResult

CONFIG_DIR = Path(__file__).parents[2] / "config"


def make(*configs: CityConfig) -> tuple[Services, CityManager, TickGate, ManualClock]:
    base = local_config()
    app = replace(base, cities=configs, server=replace(base.server, default_city=configs[0].letter))
    clock, gate = ManualClock(1000.0), TickGate()
    services = build_services(app, clock=clock)
    manager = CityManager(services, clock, _rng(), gate.sleep)
    return services, manager, gate, clock


def _rng() -> random.Random:
    return random.Random(1)


async def settle(gate: TickGate) -> None:
    """One tick of every ticker: let new tasks reach their first wait, pulse, let them run to the
    next."""
    await asyncio.sleep(0)
    gate.pulse()
    for _ in range(5):
        await asyncio.sleep(0)


def seat(services: Services, letter: str, name: str = "ALICE", team: int = 0) -> int:
    joined = services.cities[letter].join(name, team, 1000.0, identity=f"acct:{name}")
    assert isinstance(joined, JoinResult)
    return joined.player_id


A = city("A", name="ONE", map=1)
B = city("B", name="TWO", map=2)
C = city("C", name="THREE", map=3)


def test_a_new_city_appears_and_ticks() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B)
        manager.start()
        report_ = manager.reload([A, B, C])
        assert report_.added == ("C",) and report_.lines() == ["added: C"]
        assert services.cities["C"].config == C
        placed = services.lobby.place("C", None)
        assert not isinstance(placed, Denied) and placed.city == C
        ticks: list[float] = []
        services.cities["C"].tick = lambda now: ticks.append(now) or []  # type: ignore[method-assign]
        await settle(gate)
        assert ticks == [1000.0]  # the new city has its own ticker
        await manager.stop()

    asyncio.run(scenario())


def test_a_changed_empty_city_takes_the_new_configuration_at_once() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B)
        manager.start()
        old = services.cities["A"]
        renamed = replace(A, name="RENAMED", map=3)
        report_ = manager.reload([renamed, B])
        assert report_.changed == ("A",)
        assert services.cities["A"] is not old and services.cities["A"].config == renamed
        assert services.lobby.city("A") == renamed
        ticks: list[float] = []
        services.cities["A"].tick = lambda now: ticks.append(now) or []  # type: ignore[method-assign]
        await settle(gate)
        assert ticks == [1000.0]  # the replacement is ticked, once (the old ticker is gone)
        await manager.stop()

    asyncio.run(scenario())


def test_a_changed_city_in_play_is_not_touched_until_it_empties() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B)
        manager.start()
        running = services.cities["A"]
        pid = seat(services, "A")
        renamed = replace(A, name="RENAMED", map=3)
        report_ = manager.reload([renamed, B])
        assert report_.waiting == ("A",) and report_.changed == ()
        # Nothing changed under the player: same city, same config, still offered as it was.
        assert services.cities["A"] is running and running.config == A
        assert services.lobby.city("A") == A
        await settle(gate)
        assert services.cities["A"] is running and [p.id for p in running.players()] == [pid]
        # The game empties; the next tick swaps the configuration in.
        running.leave(pid, 1001.0)
        await settle(gate)
        assert services.cities["A"] is not running and services.cities["A"].config == renamed
        assert services.lobby.city("A") == renamed
        await manager.stop()

    asyncio.run(scenario())


def test_a_waiting_change_is_dropped_when_the_file_goes_back() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B)
        manager.start()
        running = services.cities["A"]
        pid = seat(services, "A")
        manager.reload([replace(A, name="RENAMED"), B])
        manager.reload([A, B])  # the file is back to what is running
        running.leave(pid, 1001.0)
        await settle(gate)
        assert services.cities["A"] is running
        await manager.stop()

    asyncio.run(scenario())


def test_a_held_slot_keeps_a_changed_city_from_being_empty() -> None:
    async def scenario() -> None:
        services, manager, gate, clock = make(A, B)
        manager.start()
        running = services.cities["A"]
        pid = seat(services, "A", "ALICE", 1)
        manager.reload([replace(A, name="RENAMED"), B])
        # The player drops and is held for /RESUME (II §13 item 4): the game is not empty.
        dropped = running.line_drop(pid, 1001.0, hold=True)
        assert dropped.record is not None
        services.held.hold(dropped.record)
        await settle(gate)
        assert services.cities["A"] is running
        clock.advance(10_000)  # the hold runs out
        await settle(gate)
        assert services.cities["A"].config.name == "RENAMED"
        await manager.stop()

    asyncio.run(scenario())


def test_a_removed_city_stops_taking_play_and_goes_when_empty() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B, C)
        manager.start()
        pid = seat(services, "C")
        report_ = manager.reload([A, B])
        assert report_.closing == ("C",)
        assert services.lobby.place("C", None) == Denied("NO SUCH CITY: C")
        assert "C" in services.cities  # its player is still playing
        assert [p.id for p in services.cities["C"].players()] == [pid]
        await settle(gate)
        assert "C" in services.cities
        services.cities["C"].leave(pid, 1001.0)
        await settle(gate)
        assert "C" not in services.cities
        await manager.stop()

    asyncio.run(scenario())


def test_a_removed_empty_city_goes_at_once_and_a_disabled_one_is_the_same() -> None:
    async def scenario() -> None:
        services, manager, _, _ = make(A, B, C)
        manager.start()
        report_ = manager.reload([A, replace(B, enabled=False), C])
        assert report_.removed == ("B",)
        assert "B" not in services.cities and services.lobby.city("B") is None
        await manager.stop()

    asyncio.run(scenario())


def test_a_city_asked_for_again_before_it_empties_stays() -> None:
    async def scenario() -> None:
        services, manager, gate, _ = make(A, B, C)
        manager.start()
        running = services.cities["C"]
        pid = seat(services, "C")
        manager.reload([A, B])
        manager.reload([A, B, C])
        assert services.lobby.city("C") == C  # offered again
        running.leave(pid, 1001.0)
        await settle(gate)
        assert services.cities["C"] is running
        await manager.stop()

    asyncio.run(scenario())


def test_a_placement_made_before_the_reload_cannot_enter_the_changed_city() -> None:
    """The client was told the old map; entering the new game with it would load the wrong one."""

    async def scenario() -> None:
        services, manager, _, _ = make(A, B)
        manager.start()
        placed = services.lobby.place("A", None)
        assert isinstance(placed, Placement)
        member = services.lobby.join("ALICE", None, lambda data: None)
        manager.reload([replace(A, map=3), B])
        assert not services.lobby.enter(member, placed)
        fresh = services.lobby.place("A", None)
        assert isinstance(fresh, Placement) and services.lobby.enter(member, fresh)
        await manager.stop()

    asyncio.run(scenario())


# --- from the files -----------------------------------------------------------------------------


def copy_config(tmp_path: Path) -> Path:
    (tmp_path / "cities").mkdir()
    for name in ("A", "B", "C"):
        (tmp_path / "cities" / f"{name}.toml").write_text(
            (CONFIG_DIR / "cities" / f"{name}.toml").read_text()
        )
    path = tmp_path / "server.toml"
    path.write_text((CONFIG_DIR / "server.toml").read_text())
    return path


def running(path: Path) -> tuple[Services, CityManager, AppConfig, TickGate]:
    app = load_config(path)
    clock, gate = ManualClock(1000.0), TickGate()
    services = build_services(app, clock=clock)
    return services, CityManager(services, clock, _rng(), gate.sleep), app, gate


def test_an_invalid_file_changes_nothing_not_even_the_valid_parts(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = copy_config(tmp_path)
        services, manager, app, _ = running(path)
        manager.start()
        before = dict(services.cities)
        (tmp_path / "cities" / "D.toml").write_text(  # a valid new city...
            (tmp_path / "cities" / "A.toml").read_text().replace('"A"', '"D"')
        )
        (tmp_path / "cities" / "C.toml").write_text("letter = = broken")  # ...and a broken file
        lines = manager.reload_file(path, app.server)
        assert len(lines) == 1 and lines[0].startswith("reload refused, nothing changed:")
        assert "C.toml" in lines[0]
        assert services.cities == before and "D" not in services.cities
        assert services.lobby.city("D") is None
        await manager.stop()

    asyncio.run(scenario())


def test_a_valid_file_is_applied_and_a_changed_server_toml_is_reported(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = copy_config(tmp_path)
        services, manager, app, _ = running(path)
        manager.start()
        (tmp_path / "cities" / "A.toml").write_text(
            (tmp_path / "cities" / "A.toml").read_text().replace("CYBERCITY ONE", "NEW NAME")
        )
        assert manager.reload_file(path, app.server) == ["changed: A"]
        assert services.cities["A"].config.name == "NEW NAME"
        path.write_text(path.read_text().replace("2399", "2400"))
        lines = manager.reload_file(path, app.server)
        assert lines[-1].startswith("server.toml differs")
        await manager.stop()

    asyncio.run(scenario())


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this platform")
def test_sighup_reloads_the_city_files_of_a_running_server(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = copy_config(tmp_path)
        app = load_config(path)
        app = replace(app, server=replace(app.server, host="127.0.0.1", port=0))
        services = build_services(app, clock=ManualClock(1000.0))
        stop, task, _ = await start(
            app, services=services, config_file=ConfigFile(path, app.server)
        )
        (tmp_path / "cities" / "A.toml").write_text(
            (tmp_path / "cities" / "A.toml").read_text().replace("CYBERCITY ONE", "VIA SIGHUP")
        )
        with LogWatch("opencs.app", "reload: changed: A") as seen:
            os.kill(os.getpid(), signal.SIGHUP)
            await seen.reached(1)
        assert services.cities["A"].config.name == "VIA SIGHUP"
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)

    asyncio.run(asyncio.wait_for(scenario(), 4 * TIMEOUT))
