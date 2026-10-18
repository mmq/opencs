"""Chat: open to all including the typist, team chat to the team only, sanitized, cut (I §8.23,
A.3b)."""

from __future__ import annotations

from fixtures.delivery import deliveries
from fixtures.lobbies import city
from fixtures.pods import report
from fixtures.spec_vectors import FULL_FRAMES
from opencs.messages import ServerMessage, client, server, server_payload
from opencs.wire.frame import build_frame
from opencs.world import chat
from opencs.world.city import City
from opencs.world.events import Event, ToAll, ToPlayer, ToTeam
from opencs.world.seating import JoinResult


def table() -> tuple[City, list[int]]:
    """Ids 0 and 1 on team 0, id 2 on team 1, id 3 on team 2."""
    c = City(city("A"))
    ids = []
    for callsign, team in (("AAA", 0), ("BBB", 0), ("CCC", 1), ("DDD", 2)):
        r = c.join(callsign, team, 0.0)
        assert isinstance(r, JoinResult)
        ids.append(r.player_id)
    for pid in ids:  # all are playing: chat reaches only clients that have reported
        c.client_message(pid, report(4), 0.5)
    return c, ids


def say(c: City, pid: int, style: int, text: bytes) -> list[tuple[int, ServerMessage]]:
    return deliveries(c, c.client_message(pid, client.Chat(style, text), 1.0))


def test_open_chat_reaches_every_client_including_the_typist() -> None:
    c, _ = table()
    out = say(c, 1, 0, b"ok")
    assert [pid for pid, _ in out] == [0, 1, 2, 3]
    assert {m for _, m in out} == {server.Chat(1, 0, b"ok")}  # sender = the typist's pod id


def test_the_echo_of_the_worked_session_matches_the_captured_frame() -> None:
    """A.3b: 'U' 0 "ok" from player 0 comes back as 'a' 0 0 "ok"."""
    c, _ = table()
    ((_, msg), *_) = say(c, 0, 0, b"ok")
    assert build_frame(server_payload(msg, 0)) == FULL_FRAMES["A3b_a"][1]


def test_team_chat_reaches_only_the_typists_team_typist_included() -> None:
    c, _ = table()
    out = say(c, 0, 1, b"hi")
    assert [pid for pid, _ in out] == [0, 1]
    assert {m for _, m in out} == {server.Chat(0, 1, b"hi")}
    assert [pid for pid, _ in say(c, 2, 1, b"alone")] == [2]  # a team of one: the typist only


def test_text_is_made_printable_and_cut_to_230_bytes() -> None:
    c, _ = table()
    out = say(c, 0, 0, b"a\x0ab\x0dc\x0ed\x00e\xff" + b"x" * 300)
    msg = out[0][1]
    assert isinstance(msg, server.Chat)
    assert msg.text.startswith(b"a b c d e ")
    assert len(msg.text) == 230
    assert say(c, 0, 0, b"y" * 230)[0][1] == server.Chat(0, 0, b"y" * 230)  # the last kept
    assert len(say(c, 0, 0, b"y" * 231)[0][1].text) == 230  # type: ignore[attr-defined]


def test_a_style_the_client_never_sends_is_dropped() -> None:
    c, _ = table()
    assert say(c, 0, 2, b"x") == []
    assert say(c, 0, 100, b"x") == []


def test_a_system_line_is_sender_100_style_0_to_everyone_by_default() -> None:
    assert chat.system_line("BOUNTY UP") == Event(server.Chat(100, 0, b"BOUNTY UP"), ToAll())
    assert chat.system_line("x", ToPlayer(3)).audience == ToPlayer(3)
    assert chat.system_line("x", ToTeam(2)).audience == ToTeam(2)


def test_a_system_line_is_sanitized_too() -> None:
    event = chat.system_line("a\nbé" + "z" * 300)
    assert isinstance(event.message, server.Chat)
    assert event.message.text.startswith(b"a b?z")
    assert len(event.message.text) == 230


def test_chat_skips_a_client_that_is_still_loading() -> None:
    # Its ring holds the 'B' repeat already (I §7.1, II §7); it would drop the line anyway, not yet
    # having been introduced to the typist (I §8.23).
    c, _ = table()
    late = c.join("EEE", 0, 0.0)
    assert isinstance(late, JoinResult)
    assert late.player_id not in [pid for pid, _ in say(c, 0, 0, b"hi")]
    assert late.player_id not in [pid for pid, _ in say(c, 0, 1, b"team")]
