"""The GEnie logon (I §5.1) driven through a session with a ManualClock."""

from __future__ import annotations

import pytest

from fixtures.sessions import connect, make_world
from opencs import policy
from opencs.session.chamber import ChamberPhase
from opencs.session.login import LoginPhase


def phase_of(h: object) -> type:
    return type(h.session.phase)  # type: ignore[attr-defined]


def test_hhh_after_modem_noise_starts_the_logon() -> None:
    h = connect(make_world())
    h.feed("ATZ\r\nATDT5551212\rOK\r")
    assert h.take() == b""  # everything before HHH is ignored: no reply, no account taken
    h.feed("HHH")
    assert h.take() == b"CONNECT\r\nU#="


def test_hhh_split_across_feeds() -> None:
    h = connect(make_world())
    for piece in (b"AT\rH", b"H", b"H"):
        assert h.take() == b""
        h.feed(piece)
    assert h.take() == b"CONNECT\r\nU#="


def test_the_bare_cr_is_skipped_and_the_first_non_empty_line_is_the_account() -> None:
    world = make_world()
    h = connect(world)
    h.feed("HHH")
    h.take()
    h.feed("\r\r\r")
    assert h.take() == b""
    h.feed("ALPHA,secret\r")
    assert h.take() == b"PASSWORD: "
    assert h.session.pilot is not None
    assert h.session.pilot.callsign == "ALPHA"
    assert h.session.pilot.account == "ALPHA"


def test_name_and_password_all_in_one_feed() -> None:
    h = connect(make_world())
    h.feed("HHH\rBRAVO,pw\r<Not Telling!>\r")
    assert h.take() == b"CONNECT\r\nU#=PASSWORD: \r\n*"
    assert h.session.pilot is not None
    assert h.session.pilot.callsign == "BRAVO"


@pytest.mark.parametrize("name", ["JOINGENIE", "joingenie", "JoinGenie"])
def test_joingenie_in_any_case_is_a_guest_with_no_password(name: str) -> None:
    h = connect(make_world())
    h.feed(f"HHH\r{name}\r")
    assert h.session.pilot is not None
    assert h.session.pilot.callsign == "JOINGENI"
    assert h.session.pilot.is_guest


def test_star_after_the_delay_when_not_telling_has_not_come() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r")
    assert h.take().endswith(b"PASSWORD: ")
    h.advance(policy.LOGIN_PROMPT_DELAY - 0.01)
    assert h.take() == b""
    h.advance(0.02)
    assert h.take() == b"\r\n*"
    # <Not Telling!> arriving later is ignored: still exactly one prompt.
    h.feed("<Not Telling!>\r")
    assert h.take() == b""
    # But only once: a second one is an ordinary unknown line at the "*" level.
    h.feed("<Not Telling!>\r")
    assert h.take() == b"\r\n*"
    h.advance(5)
    assert h.take() == b""


def test_the_prompt_is_due_exactly_at_the_delay() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r")
    h.take()
    h.advance(policy.LOGIN_PROMPT_DELAY)
    assert h.take() == b"\r\n*"


def test_star_on_not_telling_when_it_comes_before_the_delay() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r")
    h.take()
    h.advance(0.1)
    h.feed("<Not Telling!>\r")
    assert h.take() == b"\r\n*"
    h.advance(1)
    assert h.take() == b""  # the timer does not send a second one


def test_m_1380_3_enters_the_chamber() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r<Not Telling!>\r")
    h.take()
    assert phase_of(h) is LoginPhase
    h.feed("M 1380;3\r")
    assert phase_of(h) is ChamberPhase
    assert h.take() == b"\x05PRESS RETURN\r"


def test_other_lines_at_the_star_level_get_a_new_prompt_and_empty_lines_none() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r<Not Telling!>\r")
    h.take()
    h.feed("\r\r")
    assert h.take() == b""
    h.feed("M 1380;4\rHELLO\r")
    assert h.take() == b"\r\n*\r\n*"
    assert phase_of(h) is LoginPhase


def test_bye_says_goodbye_and_closes() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r<Not Telling!>\rbye\r")
    assert h.take().endswith(b"GOODBYE\r")
    assert h.io.closed_with == ["BYE"]


def test_the_account_is_refused_with_a_line_then_the_connection_closes() -> None:
    world = make_world()
    first = connect(world)
    first.feed("HHH\rALPHA,pw\r")
    second = connect(world)
    second.feed("HHH\rALPHA,pw\r")
    assert second.take().endswith(b"THAT ACCOUNT IS ALREADY LOGGED IN\r")
    assert second.io.closed_with == ["login refused"]
    assert second.session.pilot is None
    wrong = connect(world)
    wrong.feed("HHH\rALPHA,nope\r")
    assert b"ALREADY" in wrong.take()  # still online, so refused before the password is tried


def test_wrong_password_is_refused() -> None:
    world = make_world()
    a = connect(world)
    a.feed("HHH\rALPHA,pw\r")
    a.session.closed()
    b = connect(world)
    b.feed("HHH\rALPHA,wrong\r")
    assert b"WRONG PASSWORD\r" in b.take()
    assert b.io.closed


def test_garbage_before_hhh_is_bounded() -> None:
    h = connect(make_world())
    chunk = b"\x00\xff junk " * 100
    for _ in range(policy.LOGIN_PREAMBLE_LIMIT // len(chunk) + 1):
        h.feed(chunk)
    assert h.io.closed_with == ["no HHH within the preamble limit"]


def test_hhh_split_late_in_a_legal_preamble_still_works() -> None:
    h = connect(make_world())
    h.feed(b"x" * (policy.LOGIN_PREAMBLE_LIMIT - 10))
    assert not h.io.closed
    h.feed(b"HHH")
    assert h.take() == b"CONNECT\r\nU#="


def test_an_overlong_account_line_closes() -> None:
    h = connect(make_world())
    h.feed("HHH")
    h.feed(b"A" * (policy.MAX_LINE_BYTES + 1) + b"\r")
    assert h.io.closed_with == ["login line too long"]


def test_a_line_of_exactly_the_limit_is_accepted() -> None:
    h = connect(make_world())
    h.feed("HHH")
    h.feed(b"A" * policy.MAX_LINE_BYTES + b"\r")
    # Too long for a login name, but a refusal rather than the overlong path.
    assert h.io.closed_with == ["login refused"]


def test_too_many_lines_close() -> None:
    h = connect(make_world())
    h.feed("HHH")
    h.feed("\r" * policy.LOGIN_MAX_LINES)
    assert not h.io.closed
    h.feed("\r")
    assert h.io.closed_with == ["too many lines before the Chamber"]


def test_login_times_out_without_an_account() -> None:
    h = connect(make_world())
    h.advance(policy.LOGIN_TIMEOUT - 1)
    assert not h.io.closed
    h.advance(1)
    assert h.io.closed_with == ["login timed out"]


def test_a_silent_star_prompt_times_out_much_later() -> None:
    h = connect(make_world())
    h.feed("HHH\rJOINGENIE\r<Not Telling!>\r")
    h.advance(policy.LOGIN_TIMEOUT + 1)
    assert not h.io.closed
    h.advance(policy.GENIE_IDLE_TIMEOUT)
    assert h.io.closed_with == ["login timed out"]


def test_closing_frees_the_callsign() -> None:
    world = make_world()
    a = connect(world)
    a.feed("HHH\rJOINGENIE\r")
    b = connect(world)
    b.feed("HHH\rJOINGENIE\r")
    assert b.session.pilot is not None and b.session.pilot.callsign == "JOINGEN2"
    a.session.closed()
    a.session.closed()  # idempotent
    c = connect(world)
    c.feed("HHH\rJOINGENIE\r")
    assert c.session.pilot is not None and c.session.pilot.callsign == "JOINGENI"


def test_nothing_is_processed_after_a_close_request() -> None:
    h = connect(make_world())
    h.feed("HHH\rBAD,\rJOINGENIE\r")  # an empty password refuses the first account line
    assert h.io.closed
    assert h.session.pilot is None
