#!/usr/bin/env python3
"""Black-box conformance check for any CyberStrike-compatible server.

``--checks`` grades a subset and ``--server`` starts OpenCS itself. It drives two
``tools/fake_client.py`` sessions through the real Chamber flow and the binary game protocol (I
§8/B) and grades what comes back. It knows nothing of the server under test beyond its port, so it
judges every implementation by the same rules.

    uv run python tools/judge.py --server                   # start OpenCS, grade all 11 core checks
    uv run python tools/judge.py --server --checks 1,2      # only login and city selection
    uv run python tools/judge.py --port 2399 --stage 2      # an already running server, 19 checks

``--stage 2`` adds eight meta-game checks (a tower and a module order, status, bounty, territory).
Exit status is 0 if and only if every selected check passes.

What it cannot judge: anything visible (a fake client renders nothing), timing feel, and every
server policy (I §10): those need the owner and a real client. A pass means the protocol
plumbing is right, not that the game is playable.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE = ROOT / "tools" / "fake_client.py"
CONFIG = ROOT / "config" / "server.toml"
LISTENING = re.compile(r"listening on (\S+):(\d+)")

#: Checks that need only the Chamber flow: with only these selected, the clients stop at the launch.
CHAMBER_CHECKS = frozenset({1, 2})
STAGE_ONE_CHECKS = 11
ALL_CHECKS = 19


@dataclass(frozen=True)
class Check:
    number: int
    name: str
    passed: Callable[[str, str], bool]  # (ALPHA's output, BRAVO's output)


def has(out: str, pattern: str) -> bool:
    return re.search(pattern, out) is not None


CHECKS: list[Check] = [
    Check(1, "login and Chamber reached", lambda a, b: has(a, r"in the Chamber")),
    Check(
        2,
        "/PLAY answered with a city selection ('E') and the launch ('C')",
        lambda a, b: has(a, r"server chose city") and has(a, r"launched -- switching"),
    ),
    Check(
        3,
        "in-game session established (latency ping answered, I §8.7)",
        lambda a, b: has(a, r"PING REPLY"),
    ),
    Check(
        4,
        "each player introduced to the other with 'G'",
        lambda a, b: (
            has(a, r"INTRODUCED to player 'BRAVO'") and has(b, r"INTRODUCED to player 'ALPHA'")
        ),
    ),
    Check(
        5,
        "each introduced pod released from drop state: 'A' <its id> (I §7.2)",
        lambda a, b: (
            has(a, r"'A' land-pod id=\d+  \(BRAVO") and has(b, r"'A' land-pod id=\d+  \(ALPHA")
        ),
    ),
    Check(
        6,
        "pod state relayed: ALPHA receives BRAVO's 'b'",
        lambda a, b: has(a, r"'b' pod-state #\d+ id="),
    ),
    Check(
        7,
        "chat relayed to the other player",
        lambda a, b: has(b, r"CHAT from id=\d+ \(ALPHA\): 'hello'"),
    ),
    Check(
        8,
        "chat echoed to its sender (the client does not render its own)",
        lambda a, b: has(a, r"CHAT from id=\d+ \(me .*\): 'hello'"),
    ),
    Check(
        9,
        "a Grenade launch relayed to the other player ('E'-'G')",
        lambda a, b: has(a, r"GRENADE IN FLIGHT"),
    ),
    Check(10, "a leaving player removed with 'H'", lambda a, b: has(a, r"'H' REMOVE pod")),
    Check(
        11,
        "quit handshake returns to the Chamber (I §5.5)",
        lambda a, b: has(a, r"back in the Chamber after the quit"),
    ),
    # Uppercase S->C payloads are <type> <tag> <fields...>, as fake_client logs them.
    Check(12, "tower order accepted: 'O' sub 0", lambda a, b: has(b, r"rx 'O' #\d \S+ \S\S 10\b")),
    Check(13, "a guppy flies the order ('K')", lambda a, b: has(b, r"rx 'K' #1")),
    Check(
        14,
        "the tower appears for BOTH players ('M')",
        lambda a, b: has(a, r"rx 'M' #1") and has(b, r"rx 'M' #1"),
    ),
    Check(
        15,
        "module order accepted: 'E'-'P' with ok != 0",
        lambda a, b: has(b, r"rx 'E-P' #\d 45 \S\S 50 \S\S (?!10\b)\S\S"),
    ),
    Check(16, "the module is delivered as a drop ('P')", lambda a, b: has(b, r"rx 'P' #1")),
    Check(17, "status sent: score/multiplier 'D'-'S'", lambda a, b: has(b, r"rx 'D-S' #1")),
    Check(18, "bounty sent: 'E'-'B'", lambda a, b: has(b, r"rx 'E-B' #1")),
    Check(19, "territory grid sent: 'T'", lambda a, b: has(b, r"rx 'T' #1")),
]


def parse_checks(text: str) -> list[int]:
    """``"1,2,5"`` -> ``[1, 2, 5]``, each within 1..19."""
    numbers = sorted({int(part) for part in text.split(",") if part.strip()})
    bad = [n for n in numbers if not 1 <= n <= ALL_CHECKS]
    if bad or not numbers:
        raise argparse.ArgumentTypeError(f"checks are numbered 1..{ALL_CHECKS}, got {text!r}")
    return numbers


def run_client(
    port: int, name: str, play: str, extra: list[str], quit_after: float
) -> subprocess.Popen[str]:
    cmd = [
        sys.executable,
        str(FAKE),
        "--port",
        str(port),
        "--name",
        name,
        "--quiet",
        "--quit-after",
        str(quit_after),
        *(["--play", play] if play else []),
        *extra,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


class ManagedServer:
    """``python -m opencs --port 0`` started for the duration of a judging run."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "opencs",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--config",
                str(CONFIG),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self.port = self._wait_for_port()
        self._drain = threading.Thread(target=self._drain_output, daemon=True)
        self._drain.start()

    def _wait_for_port(self) -> int:
        assert self.proc.stdout is not None
        watchdog = threading.Timer(30.0, self.proc.kill)  # a hung start becomes EOF below
        watchdog.start()
        try:
            for line in self.proc.stdout:
                self.lines.append(line)
                found = LISTENING.search(line)
                if found:
                    return int(found.group(2))
        finally:
            watchdog.cancel()
        raise SystemExit("the server never said it was listening:\n" + "".join(self.lines))

    def _drain_output(self) -> None:
        assert self.proc.stdout is not None
        self.lines.extend(self.proc.stdout)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._drain.join(timeout=5)


def grade(port: int, selected: list[int], stage: int, play: str) -> tuple[int, str]:
    """Run two fake clients against ``port``; return (failures, report)."""
    chamber_only = set(selected) <= CHAMBER_CHECKS
    if stage == 2 and not play:
        # The fake client always stands in RED's corner. On another team it would be ordering
        # inside an enemy base, which a correct server refuses (I §9.4.3 no-drop zones).
        play = "B RED"
    orders = "grenade@8" + (",tower@10,module@12" if stage == 2 else "")
    long = stage == 2  # guppy flights need time
    first = ["--say", "hello", "--lobby", "/CITY,/WHO"]
    second = ["--order", orders]
    if chamber_only:
        first.append("--stop-after-launch")
        second.append("--stop-after-launch")
    p1 = run_client(port, "ALPHA", play, first, 60 if long else 20)
    time.sleep(3)
    p2 = run_client(port, "BRAVO", play, second, 50 if long else 12)
    out2 = p2.communicate(timeout=120)[0]
    out1 = p1.communicate(timeout=120)[0]
    lines = []
    failures = 0
    for check in CHECKS:
        if check.number not in selected:
            continue
        ok = check.passed(out1, out2)
        failures += not ok
        lines.append(f"{'ok  ' if ok else 'FAIL'} {check.number:2d} {check.name}")
    lines.append(f"\n{failures} failure(s) of {len(lines)}")
    if failures:

        def keep(out: str) -> str:
            return "\n".join(x for x in out.splitlines() if "'b' pod-state" not in x)

        lines.append("\n--- ALPHA ---\n" + keep(out1)[-3000:])
        lines.append("\n--- BRAVO ---\n" + keep(out2)[-4000:])
    return failures, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, help="port of a running server")
    ap.add_argument(
        "--server", action="store_true", help="start `python -m opencs --port 0` and stop it after"
    )
    ap.add_argument("--play", default="", help="arguments to /PLAY (default: none)")
    ap.add_argument(
        "--stage",
        type=int,
        choices=(1, 2),
        default=1,
        help="2 adds the meta-game: a tower order and a module order by BRAVO",
    )
    ap.add_argument(
        "--checks", type=parse_checks, help="grade only these checks, e.g. 1,2 (default: all)"
    )
    a = ap.parse_args()
    if a.server == (a.port is not None):
        ap.error("give exactly one of --server and --port")

    stage = a.stage
    selected = a.checks or list(range(1, (ALL_CHECKS if stage == 2 else STAGE_ONE_CHECKS) + 1))
    if any(n > STAGE_ONE_CHECKS for n in selected):
        stage = 2  # a meta-game check needs the meta-game played

    server = ManagedServer() if a.server else None
    try:
        failures, report = grade(server.port if server else a.port, selected, stage, a.play)
    finally:
        if server:
            server.stop()
    print(report)
    if failures and server:
        print("\n--- SERVER ---\n" + "".join(server.lines)[-3000:])
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
