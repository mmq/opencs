"""The operator's commands over a running server (M12): status, announce, kick, guppy home,
shutdown, reload. Text in, text out; the admin channel only carries it."""

from __future__ import annotations

import asyncio
import logging
import random

import pytest

from fixtures.pods import report
from fixtures.sessions import Harness, World, connect, enter_lobby, make_world, play
from opencs import policy
from opencs.cities import CityManager
from opencs.control import SHUTDOWN_NOTICE, Control
from opencs.lobby import replies
from opencs.messages import ClientMessage, WirePos, client, client_payload, server
from opencs.store.base import ProgressRecord
from opencs.wire.frame import build_frame

ALICE = "ALICE,SECRET"


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def control(
    world: World, stop: asyncio.Event | None = None, delay: object = None
) -> tuple[Control, asyncio.Event]:
    stop = stop or asyncio.Event()
    manager = CityManager(world.services, world.clock, random.Random(1))
    kwargs = {} if delay is None else {"delay": delay}
    return Control(world.services, manager, stop, None, **kwargs), stop  # type: ignore[arg-type]


def flying(world: World, account: str = "JOINGENIE", team: int = 0) -> Harness:
    h = play(world, "A", team, account)
    h.feed(frame(report(4)))
    h.feed(frame(report(0)))
    h.io.messages.clear()
    return h


# --- status ------------------------------------------------------------------------------------


def test_status_counts_connections_players_by_team_and_held_games() -> None:
    world = make_world()
    ctl, _ = control(world)
    flying(world, ALICE, 1)
    flying(world, "BOB,SECRET", 0)
    enter_lobby(world, "CAROL,SECRET")
    world.clock.advance(125)
    lines = ctl.status()
    assert lines[0] == "uptime: 125 s"
    assert lines[1] == "connections: 3 (in game 2, Chamber 1, logon 0)"
    assert "city A CITY A: 2 players (RED 1, BLUE 1)" in lines
    assert "city B CITY B: 0 players" in lines
    assert lines[-1] == "held games: 0"


def test_status_lists_a_held_game_with_its_time_left() -> None:
    world = make_world()
    ctl, _ = control(world)
    h = flying(world, ALICE, 1)
    h.session.closed()  # the line drops
    world.clock.advance(100)
    lines = ctl.status()
    assert lines[-2:] == [
        "held games: 1",
        f"  ALICE city A BLUE, {policy.HOLD_SECONDS - 100:.0f} s left, can be resumed",
    ]


def test_status_counts_a_session_still_at_the_logon() -> None:
    world = make_world()
    ctl, _ = control(world)
    connect(world)
    assert ctl.status()[1] == "connections: 1 (in game 0, Chamber 0, logon 1)"


# --- announce ----------------------------------------------------------------------------------


def test_announce_reaches_players_in_game_and_the_chamber_and_no_one_else() -> None:
    world = make_world()
    ctl, _ = control(world)
    flyer = flying(world, ALICE, 1)
    loading = play(world, "A", 0, "BOB,SECRET")  # seated but has not sent its first report
    loading.io.messages.clear()
    chamber = enter_lobby(world, "CAROL,SECRET")
    assert ctl.execute("announce Server restarts at 9") == ["announced to 2"]
    assert flyer.io.messages == [server.Chat(100, 0, b"Server restarts at 9")]  # sender 100
    # still loading: its ring would drop it (our audience rule (clients that have reported))
    assert loading.io.messages == []
    assert chamber.take() == replies.render([replies.body("*** Server restarts at 9")])
    assert flyer.take() == b"" and loading.take() == b""  # no Chamber text to a game


def test_announce_text_is_made_printable_and_cut_to_what_the_client_takes() -> None:
    world = make_world()
    ctl, _ = control(world)
    flyer = flying(world, ALICE, 1)
    chamber = enter_lobby(world, "CAROL,SECRET")
    ctl.announce("a\x05b\x01 ü" + "x" * 400)
    (sent,) = flyer.io.messages
    assert isinstance(sent, server.Chat) and sent.sender == 100
    assert sent.text.startswith(b"a b  ?x") and len(sent.text) == 230  # MAX_CHAT_BYTES
    expected = "a b  ?" + "x" * (230 - len("a b  ?"))
    assert chamber.take() == replies.render([replies.body(f"*** {expected}")])  # nothing armed


def test_announce_needs_a_text() -> None:
    ctl, _ = control(make_world())
    assert ctl.execute("announce") == ["usage: announce <text>"]
    assert ctl.execute("announce   ") == ["usage: announce <text>"]


# --- kick --------------------------------------------------------------------------------------


def test_kick_in_game_sends_the_notice_and_c_and_keeps_the_connection() -> None:
    world = make_world()
    ctl, _ = control(world)
    h = flying(world, ALICE, 1)
    assert ctl.execute("kick alice be nice") == ["kicked ALICE (1 connection(s))"]
    assert h.io.messages == [
        server.Chat(100, 0, b"REMOVED BY AN ADMINISTRATOR: be nice"),
        server.QuitAck(),
    ]
    assert not h.io.closed and world.services.cities["A"].players() == []


def test_kick_in_the_chamber_says_why_and_closes_with_the_default_reason() -> None:
    world = make_world()
    ctl, _ = control(world)
    h = enter_lobby(world, ALICE)
    ctl.execute("kick ALICE")
    said = h.take()
    assert said.count(policy.ADMIN_KICK_NOTICE.encode()) == 1  # not "X: X"
    assert h.io.closed


def test_kick_leaves_other_pilots_alone() -> None:
    world = make_world()
    ctl, _ = control(world)
    a = enter_lobby(world, ALICE)
    b = enter_lobby(world, "BOB,SECRET")
    ctl.execute("kick bob")
    assert b.io.closed and not a.io.closed


def test_kick_of_nobody_or_nothing() -> None:
    ctl, _ = control(make_world())
    assert ctl.execute("kick ghost") == ["no pilot GHOST is connected"]
    assert ctl.execute("kick") == ["usage: kick <callsign> [reason]"]


# --- guppy -------------------------------------------------------------------------------------


class Sink:
    def __init__(self) -> None:
        self.got: list[server.ServerMessage] = []

    def send(self, msg: server.ServerMessage) -> None:
        self.got.append(msg)


def guppy_world() -> tuple[World, Control, dict[int, Sink]]:
    world = make_world()
    ctl, _ = control(world)
    city = world.services.cities["A"]
    sinks = {}
    for team in (0, 1):
        joined = city.join(f"P{team}", team, 1.0)
        assert not hasattr(joined, "reason")
        pid = joined.player_id  # type: ignore[union-attr]
        city.client_message(pid, report(0), 2.0)
        sinks[pid] = Sink()
        world.services.router.register("A", pid, sinks[pid])
    pid0 = next(iter(sinks))
    city.client_message(pid0, client.TowerOrder(WirePos(2, 2, 100, 20, 200), 0, 0), 5.0)
    for sink in sinks.values():
        sink.got.clear()
    return world, ctl, sinks


@pytest.mark.parametrize("team", ["red", "RED", "0"])
def test_guppy_home_warps_the_teams_guppy_for_every_holder(team: str) -> None:
    world, ctl, sinks = guppy_world()
    assert ctl.execute(f"guppy {team} home") == ["city A: RED guppy warped home, 2 client(s) told"]
    assert [type(m) for s in sinks.values() for m in s.got] == [server.MobileWarp] * 2
    assert world.services.cities["A"].orders.logistics.guppy(0) is not None


def test_guppy_home_of_a_guppy_nobody_holds_says_it_was_reset() -> None:
    world, ctl, sinks = guppy_world()
    for p in world.services.cities["A"].players():
        p.view.legs.clear()
    assert ctl.execute("guppy red home") == ["city A: RED guppy warped home, 0 client(s) told"]
    assert all(s.got == [] for s in sinks.values())


def test_guppy_home_with_no_guppy_or_a_bad_team() -> None:
    _, ctl, sinks = guppy_world()
    assert ctl.execute("guppy blue home") == ["no BLUE guppy exists in any city"]
    assert all(s.got == [] for s in sinks.values())
    usage = "usage: guppy <RED|BLUE|GREEN|BLACK|0-3> home"
    assert ctl.execute("guppy") == [usage]
    assert ctl.execute("guppy purple home") == [usage]
    assert ctl.execute("guppy red") == [usage]
    assert ctl.execute("guppy 4 home") == [usage]


# --- shutdown ----------------------------------------------------------------------------------


def test_shutdown_now_sets_the_stop_event() -> None:
    async def scenario() -> None:
        ctl, stop = control(make_world())
        assert ctl.execute("shutdown") == ["shutting down now"]
        assert stop.is_set()

    asyncio.run(scenario())


def test_shutdown_with_a_delay_announces_and_stops_when_the_delay_has_passed() -> None:
    async def scenario() -> None:
        release: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        waited: list[float] = []

        async def delay(seconds: float) -> None:
            waited.append(seconds)
            await release

        world = make_world()
        ctl, stop = control(world, delay=delay)
        flyer = flying(world, ALICE, 1)
        assert ctl.execute("shutdown 45") == ["shutting down in 45 s"]
        assert flyer.io.messages == [
            server.Chat(100, 0, f"{SHUTDOWN_NOTICE} IN 45 SECONDS".encode())
        ]
        await asyncio.sleep(0)
        assert waited == [45] and not stop.is_set()
        release.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert stop.is_set()

    asyncio.run(scenario())


def test_a_pending_shutdown_can_be_canceled_or_replaced() -> None:
    async def scenario() -> None:
        never: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def delay(seconds: float) -> None:
            await never

        world = make_world()
        ctl, stop = control(world, delay=delay)
        flyer = flying(world, ALICE, 1)
        assert ctl.execute("shutdown cancel") == ["no shutdown is pending"]
        ctl.execute("shutdown 30")
        flyer.io.messages.clear()
        assert ctl.execute("shutdown cancel") == ["shutdown canceled"]
        assert flyer.io.messages == [server.Chat(100, 0, b"SHUTDOWN CANCELED")]
        never.cancel()
        await asyncio.sleep(0)
        assert not stop.is_set()

    asyncio.run(scenario())


def test_shutdown_arguments_are_checked() -> None:
    async def scenario() -> None:
        ctl, stop = control(make_world())
        assert ctl.execute("shutdown soon") == ["usage: shutdown [seconds|cancel]"]
        assert ctl.execute("shutdown -5") == ["usage: shutdown [seconds|cancel]"]
        assert ctl.execute(f"shutdown {policy.ADMIN_MAX_SHUTDOWN_SECONDS + 1}") == [
            f"at most {policy.ADMIN_MAX_SHUTDOWN_SECONDS} seconds"
        ]
        assert not stop.is_set()
        ctl.close()

    asyncio.run(scenario())


# --- reload, help, input -----------------------------------------------------------------------


def test_reload_without_a_config_file() -> None:
    ctl, _ = control(make_world())
    assert ctl.execute("reload") == ["no config file is known: nothing to reload"]


def test_help_lists_every_command_and_unknown_words_are_refused() -> None:
    ctl, _ = control(make_world())
    text = "\n".join(ctl.execute("help"))
    for word in ("status", "announce", "kick", "guppy", "shutdown", "reload", "help", "quit"):
        assert word in text
    assert ctl.execute("frobnicate now") == ["unknown command 'frobnicate': try help"]
    assert ctl.execute("") == [] and ctl.execute("   ") == []


@pytest.mark.parametrize(
    "line",
    ["STATUS", "Kick", "kick  ", "announce \x00\x01", "guppy \x00 home", "shutdown 99999999999999",
     "reload extra", "help me", "status " + "x" * 5000, "kick éé é",
     "shutdown ²", "guppy ² home", "guppy ³ home"],
)  # fmt: skip
def test_no_operator_input_raises(line: str) -> None:
    async def scenario() -> None:
        ctl, _ = control(make_world())
        ctl.execute(line)
        ctl.close()

    asyncio.run(scenario())


# --- accounts --------------------------------------------------------------------------------


def test_the_operator_creates_an_account_and_its_password_is_shown_once_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = make_world()
    ctl, _ = control(world)
    with caplog.at_level(logging.DEBUG):
        reply = ctl.execute("create-account newpilot")
    assert len(reply) == 1 and reply[0].startswith("created NEWPILOT; password (shown once): ")
    password = reply[0].rsplit(" ", 1)[1]
    assert password not in caplog.text
    h = enter_lobby(world, f"NEWPILOT,{password}")
    assert h.session.pilot is not None and h.session.pilot.account == "NEWPILOT"
    assert ctl.execute("create-account NEWPILOT") == ["refused: THAT NAME IS TAKEN"]
    assert ctl.execute("create-account ADMIN") == ["refused: THAT NAME IS RESERVED"]
    assert ctl.execute("accounts") == [
        f"accounts: 1 of at most {policy.MAX_ACCOUNTS}; registration open"
    ]


def test_the_operator_resets_a_password(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world()
    ctl, _ = control(world)
    enter_lobby(world, "ALICE,oldsecret").session.closed()
    with caplog.at_level(logging.DEBUG):
        reply = ctl.execute("reset-password alice")
    password = reply[0].rsplit(" ", 1)[1]
    assert password not in caplog.text
    assert enter_lobby(world, f"ALICE,{password}").session.pilot is not None
    assert ctl.execute("reset-password NOBODY") == ["refused: NO SUCH ACCOUNT"]


def test_progress_and_accounts_are_wiped_only_while_the_player_is_offline() -> None:
    world = make_world()
    ctl, _ = control(world)
    h = enter_lobby(world, "ALICE,secret")
    world.progress.save("acct:ALICE", ProgressRecord((1,) * 9, 0, 5, 6, 7))
    assert ctl.execute("reset-progress ALICE") == ["refused: ALICE is online; kick it first"]
    assert ctl.execute("delete-account ALICE") == ["refused: ALICE is online; kick it first"]
    h.session.closed()
    assert ctl.execute("reset-progress alice") == ["progress of ALICE erased"]
    assert world.progress.load("acct:ALICE") is None
    assert ctl.execute("delete-account alice") == ["deleted ALICE"]
    assert world.identity.account_exists("ALICE") is False
    assert ctl.execute("delete-account ALICE") == ["refused: no account ALICE"]
    assert ctl.execute("delete-account") == ["usage: delete-account <name>"]
