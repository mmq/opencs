"""The in-game phase's input side: frames in, bad input counted over a window (DESIGN §5)."""

from __future__ import annotations

import pytest

from fixtures.captures import load_capture
from fixtures.sessions import Harness, make_world, play
from opencs import policy
from opencs.messages import ClientMessage, DecodeError, client, client_payload, decode_client
from opencs.session.ingame import InGamePhase
from opencs.wire.frame import FrameReader, build_frame

# A client 'b' that ends after ``power`` (I §4.1):
# 26 of these were captured, 24 of them one a second.
SHORT_B = bytes.fromhex("62 10 21 43 48 41 f5 85 14 88 88 13 40 74 18 11 10 92 a0")


def in_game() -> tuple[Harness, list[ClientMessage]]:
    h = play(make_world())
    seen: list[ClientMessage] = []
    phase = h.session.phase
    assert isinstance(phase, InGamePhase)
    phase.handle = seen.append  # type: ignore[method-assign]
    return h, seen


def frame(payload: bytes) -> bytes:
    return build_frame(payload, terminator=b"\x0a")


def test_frames_are_decoded_and_handed_to_handle() -> None:
    h, seen = in_game()
    h.feed(frame(b"EN") + frame(b"C"))
    assert seen == [client.Ping(), client.Quit()]
    assert not h.io.closed


def test_frames_split_across_feeds() -> None:
    h, seen = in_game()
    data = frame(b"EN")
    for i in range(len(data)):
        h.feed(data[i : i + 1])
    assert seen == [client.Ping()]


def test_the_july_build_trailing_nul_in_b_frames_is_accepted_for_a_whole_run() -> None:
    h, seen = in_game()
    frames = [f for f in load_capture("2026-07-29-030741_002-city-name.txt") if f.direction == "C"]
    assert any(f.raw[5:6] == b"b" and f.raw[-1] == 0 for f in frames)
    for f in frames:
        h.clock.set(100.0 + f.t)
        h.feed(f.raw)
    assert len(seen) == len(frames)
    assert not h.io.closed
    assert h.session.phase._bad.total == 0  # type: ignore[attr-defined]


def test_undecodable_real_client_b_frames_at_one_a_second_do_not_close() -> None:
    # 24 in a row, one a second, as captured.
    h, seen = in_game()
    for _ in range(24):
        h.feed(frame(SHORT_B))
        h.advance(1.0)
    assert seen == []
    assert not h.io.closed


def test_a_burst_of_bad_frames_closes_but_the_last_allowed_one_does_not() -> None:
    h, _ = in_game()
    for _ in range(policy.MAX_BAD_INPUT):
        h.feed(frame(SHORT_B))
    assert not h.io.closed
    h.feed(frame(SHORT_B))
    assert h.io.closed_with == ["too much bad input"]


def test_the_count_is_a_window_not_a_lifetime_total() -> None:
    h, _ = in_game()
    for _ in range(3):
        for _ in range(policy.MAX_BAD_INPUT):
            h.feed(frame(SHORT_B))
        h.advance(policy.BAD_INPUT_WINDOW)
    assert not h.io.closed


def test_framing_garbage_counts_toward_the_same_limit() -> None:
    h, _ = in_game()
    # Each is a header with a good CK pair but a wrong payload checksum: one bad frame apiece.
    bad = bytearray(frame(b"b" * 10))
    bad[-3] ^= 0x01
    for _ in range(policy.MAX_BAD_INPUT + 1):
        h.feed(bytes(bad))
    assert h.io.closed_with == ["too much bad input"]


def test_a_stray_header_does_not_hold_back_real_frames_for_ever() -> None:
    h, seen = in_game()
    stray = b"CS" + bytes([0x20, 0x20 ^ 0xFF, 0x90])  # claims 0x80 bytes that never come
    h.feed(stray + frame(b"EN"))
    assert seen == []
    h.advance(policy.FRAME_STALL_SECONDS - 0.1)
    assert seen == []
    h.advance(0.2)
    assert seen == [client.Ping()]
    h.feed(frame(b"C"))
    assert seen == [client.Ping(), client.Quit()]
    assert not h.io.closed


def test_a_frame_split_within_the_stall_window_completes_and_a_completed_frame_resets_it() -> None:
    h, seen = in_game()
    ping, quit_ = frame(b"EN"), frame(b"C")
    h.feed(ping + quit_[:4])  # a whole frame, then the start of the next
    h.advance(policy.FRAME_STALL_SECONDS - 0.5)
    h.feed(quit_[4:])
    assert seen == [client.Ping(), client.Quit()]
    h.advance(policy.FRAME_STALL_SECONDS * 3)  # nothing pending: nothing to give up
    assert not h.io.closed


@pytest.mark.parametrize("junk", [b"\x00" * 3000, b"hello world" * 200])
def test_noise_without_headers_is_skipped_silently(junk: bytes) -> None:
    h, seen = in_game()
    h.feed(junk)
    h.feed(frame(b"EN"))
    assert seen == [client.Ping()]
    assert not h.io.closed


def test_a_growing_partial_frame_is_still_given_up_when_the_stall_began_long_ago() -> None:
    h, seen = in_game()
    stray = b"CS" + bytes([0x20, 0x20 ^ 0xFF, 0x90])  # claims 0x80 bytes that never come
    h.feed(stray + frame(b"EN"))  # the stall clock starts here
    h.advance(policy.FRAME_STALL_SECONDS - 1.0)
    h.feed(b"\x00")  # the partial grows: this must not restart the clock
    assert seen == []
    h.advance(1.0)
    assert seen == [client.Ping()]


def test_a_partial_frame_that_finishes_in_time_is_not_given_up() -> None:
    h, seen = in_game()
    ping, quit_ = frame(b"EN"), frame(b"C")
    h.feed(ping + quit_[:3])  # a whole frame completed: nothing is stalled yet
    h.advance(2.0)
    h.feed(quit_[3:5])  # the partial grows but does not complete
    h.advance(policy.FRAME_STALL_SECONDS - 1.0)  # 3.0 s after the first feed, 2.0 after the second
    h.feed(quit_[5:])
    assert seen == [client.Ping(), client.Quit()]


# --- the relay budgets (policy.RELAY_LIMIT_*) ----------------------------------------------------


CHAT = frame(client_payload(client.Chat(0, b"hi")))


def relayed_in_game() -> tuple[Harness, list[ClientMessage]]:
    """A session whose hand-offs to the city are recorded; ``handle`` is the real one."""
    h = play(make_world())
    seen: list[ClientMessage] = []
    phase = h.session.phase
    assert isinstance(phase, InGamePhase)
    phase._to_city = seen.append  # type: ignore[method-assign]
    return h, seen


def test_chat_beyond_its_burst_is_dropped_and_counted_not_relayed() -> None:
    h, seen = relayed_in_game()
    burst, _ = policy.RELAY_LIMIT_CHAT
    h.feed(b"".join(CHAT for _ in range(burst + 5)))
    assert len(seen) == burst
    assert h.session.phase._bad.total == 5  # type: ignore[attr-defined]
    assert not h.io.closed


def test_hit_reports_beyond_their_burst_are_dropped_and_counted_not_relayed() -> None:
    # Each credited hit lands in the victim's outbox (I §9.1.4), so it is budgeted too.
    h, seen = relayed_in_game()
    burst, _ = policy.RELAY_LIMIT_HIT
    hit = frame(client_payload(client.HitReport(1, 10)))
    h.feed(hit * (burst + 3))
    assert len(seen) == burst
    assert h.session.phase._bad.total == 3  # type: ignore[attr-defined]


def test_the_budget_refills_with_time() -> None:
    h, seen = relayed_in_game()
    h.feed(CHAT * (policy.RELAY_LIMIT_CHAT[0] + 1))
    before = len(seen)
    h.advance(1.0 / policy.RELAY_LIMIT_CHAT[1])
    h.feed(CHAT)
    assert len(seen) == before + 1


def test_a_sustained_flood_closes_the_flooder() -> None:
    h, seen = relayed_in_game()
    burst, _ = policy.RELAY_LIMIT_CHAT
    h.feed(b"".join(CHAT for _ in range(burst + policy.MAX_BAD_INPUT + 1)))
    assert len(seen) == burst
    assert h.io.closed_with == ["too much bad input"]


@pytest.mark.parametrize(
    "name",
    [
        "2026-09-03-123853_002-hit-dedup-p2.txt",  # the densest shooting: 17 shots in 10 s
        "2026-10-16-142644_002-sentinel-down-p1.txt",
        "2026-09-15-102453_001-bounty-payout-p1.txt",  # 15 hit reports among 46 Sentinel shots
        "2026-10-16-113841_002-play-p1.txt",
        "2026-10-13-140200_002-phase6-live-p1.txt",  # the most 'b' and chat
        "2026-10-16-154139_004-grenade-relay-p1.txt",
    ],
)
def test_a_real_captures_stream_is_never_limited(name: str) -> None:
    h, seen = relayed_in_game()
    sent_frames = [f for f in load_capture(name) if f.direction == "C"]
    decoded = 0
    for f in sent_frames:
        h.clock.set(100.0 + f.t)
        h.feed(f.raw)
    reader = FrameReader()
    for f in sent_frames:
        for fr in reader.feed(f.raw):
            try:
                m = decode_client(fr.payload)
            except DecodeError:
                continue
            if not isinstance(m, client.Ping | client.Quit | client.Acks | client.Flags):
                decoded += 1
    assert decoded > 0 and len(seen) == decoded
    assert not h.io.closed
