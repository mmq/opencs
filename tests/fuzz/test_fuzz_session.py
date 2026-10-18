"""Random valid client messages into in-game sessions: nothing raises, every message the world
emits encodes, and the id invariants hold (DESIGN §5, I §6.2-§7.3)."""

from __future__ import annotations

import random

import pytest

from fixtures.fuzzing import CLIENT_TYPES, ClientModel, ErrorLog, check_city, random_client_message
from fixtures.pods import report
from fixtures.sessions import Harness, World, make_world, play
from opencs.messages import ClientMessage, client, client_payload
from opencs.wire.frame import build_frame

ALLOWED_CLOSES = {"too much bad input", "line silent", "no QUIT after the quit handshake"}


def frame(msg: ClientMessage) -> bytes:
    return build_frame(client_payload(msg), terminator=b"\x0a")


def run(seed: int, steps: int, players: int = 3) -> None:
    rng = random.Random(seed)
    world = make_world()
    city = world.services.cities["A"]
    live: list[tuple[Harness, ClientModel]] = []

    def join(n: int) -> None:
        h = play(world, "A", rng.randrange(4))  # JOINGENIE: a guest name players share
        model = ClientModel(h.io, name=f"P{n}")
        h.feed(frame(report(4)))
        live.append((h, model))

    for n in range(players):
        join(n)
    joined = players
    with ErrorLog() as errors:
        for step in range(steps):
            h, _ = rng.choice(live)
            if not h.io.closed:
                known = [*city.live, *(p.id for p in city.players())]
                r = rng.random()
                if r < 0.01:
                    kinds: list[type[ClientMessage]] = [client.Quit]
                elif r < 0.4:
                    kinds = [client.PodState, client.Shot]  # the traffic of real play
                else:
                    kinds = [k for k in CLIENT_TYPES if k is not client.Quit]
                h.feed(frame(random_client_message(rng, kinds, known)))
            world.clock.advance(rng.choice([0.0, 0.05, 0.2, 1.0, 1.5]))
            if rng.random() < 0.3:
                world.services.router.deliver(city, city.tick(world.clock.now()))
            for each, _ in live:
                each.session.tick(world.clock.now())
            if rng.random() < 0.02 and len(live) > 1:  # someone's line drops, someone arrives
                gone, _ = live.pop(rng.randrange(len(live)))
                gone.session.closed()
            if rng.random() < 0.03 and len(city.players()) < 6:
                join(joined)
                joined += 1
            problems = check_city(city)
            for each, model in live:
                model.absorb()
                problems += model.violations
                reason = each.io.closed_with
                assert not reason or reason[0] in ALLOWED_CLOSES, (
                    f"seed {seed} step {step}: {reason}"
                )
            assert not problems, f"seed {seed} step {step}: {problems}"
            assert not errors.records, f"seed {seed} step {step}:\n{errors.describe()}"


@pytest.mark.parametrize("seed", range(30))
def test_random_valid_messages_keep_every_invariant(seed: int) -> None:
    run(seed, 600)


def _unused(world: World) -> None:  # keeps the import used for type checkers
    del world
