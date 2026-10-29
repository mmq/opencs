"""Traffic: what a connection sent, for the log at close (DESIGN §6)."""

from __future__ import annotations

from opencs.net.traffic import Traffic


def test_totals_and_the_average_over_the_connection() -> None:
    traffic = Traffic(100.0, windows=(1.0,))
    traffic.sent(30, 100.0, frame=False)
    for i in range(10):
        traffic.sent(23, 101.0 + i, frame=True)
    assert traffic.bytes == 260
    assert traffic.frames == 10
    assert traffic.summary(120.0).startswith("sent 260 bytes, 10 frames in 20 s: avg 13 B/s;")


def test_the_peak_is_the_busiest_stretch_averaged_over_its_length() -> None:
    traffic = Traffic(0.0, windows=(1.0, 10.0))
    for t in (0.1, 0.5, 0.9):  # 3 frames in the first second
        traffic.sent(20, t, frame=True)
    for t in (2.0, 2.5, 3.0, 3.5, 4.0, 4.5):  # 2 a second for a while
        traffic.sent(25, t, frame=True)
    assert traffic.summary(10.0) == (
        "sent 210 bytes, 9 frames in 10 s: avg 21 B/s;"
        " peak 60 B/s, 3.0 frames/s (1 s); peak 21 B/s, 0.9 frames/s (10 s)"
    )


def test_a_short_connection_does_not_inflate_the_average() -> None:
    traffic = Traffic(0.0, windows=(1.0,))
    traffic.sent(500, 0.0, frame=False)
    assert "in 0 s: avg 500 B/s;" in traffic.summary(0.2)


def test_nothing_sent() -> None:
    assert Traffic(0.0, windows=(1.0,)).summary(5.0) == (
        "sent 0 bytes, 0 frames in 5 s: avg 0 B/s; peak 0 B/s, 0.0 frames/s (1 s)"
    )
