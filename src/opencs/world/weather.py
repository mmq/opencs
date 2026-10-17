"""A city's weather: its condition, its program and what a client is told (I §8.20, I §9.3.5, I
§7.3).

The condition is three numbers: the palette 0-4 (``'R'`` subs 0 and 1), the rain 0-3 (subs 2 and 3)
and lightning off or on (sub 4). It is city-wide state, not an entity, so it is a ``CityState``: a
client is sent it on its first report and again whenever it has changed (``entities.inform``), and
what it is sent is only what differs from the copy it holds.

Two things change it. The Weather module cycles the palette (``cycle``). The city's program (its
``ambient`` steps, looped from the moment the game starts) applies a step when it is due (``tick``).
A client that is present when the condition changes is sent a *transition* (a palette crossfade,
a rain ramp); one that arrives later is sent the condition *at once*.

The client's crossfade takes about 50 s, and a crossfade begun while one runs, back to the palette
it started from, is dropped (I §8.20, observed). So palette changes are kept at least
``CROSSFADE_SECONDS`` apart: a program step that would change the light waits until the last
change is that old, and a Weather module used while a crossfade runs is ignored.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence

from opencs import policy
from opencs.config import AmbientStep
from opencs.messages import ServerMessage, limits, server
from opencs.world.events import Event, ToPlayer
from opencs.world.view import ClientView

#: The key under which a ``ClientView`` records the condition it was sent.
WEATHER_KEY = "weather"

#: ``(palette, rain, lightning)``: what a client's screen shows.
type Condition = tuple[int, int, int]

#: What a fresh client already shows: the client's own default palette, no rain, no lightning. A
#: client fact (``limits.CLIENT_DEFAULT_PALETTE``), not the city's starting condition.
CLIENT_CONDITION: Condition = (limits.CLIENT_DEFAULT_PALETTE, 0, 0)

#: The ``'R'`` subs (I §8.20).
_PALETTE_NOW, _PALETTE_FADE, _RAIN_NOW, _RAIN_RAMP, _LIGHTNING = 0, 1, 2, 3, 4


def _messages(
    before: Condition, after: Condition, subs: tuple[int, int, int]
) -> list[ServerMessage]:
    """``'R'`` messages taking a screen from ``before`` to ``after``: one per part that differs,
    with the given subs for the palette, the rain and the lightning."""
    palette_sub, rain_sub, lightning_sub = subs
    out: list[ServerMessage] = []
    if after[0] != before[0]:
        out.append(server.Ambient(palette_sub, after[0]))
    if after[1] != before[1]:
        out.append(server.Ambient(rain_sub, after[1]))
    if after[2] != before[2]:
        out.append(server.Ambient(lightning_sub, after[2]))
    return out


class Weather:
    """One city's condition and program."""

    def __init__(self, program: Sequence[AmbientStep] = ()) -> None:
        self._program = tuple(program)
        self.palette = policy.DEFAULT_PALETTE
        self.rain = 0
        self.lightning = 0
        #: When the next step is due, or None before the game starts / with no program.
        self._due: float | None = None
        self._next = 0
        #: When the palette last changed (by either cause), and until when a program crossfade
        #: runs on the clients.
        self._palette_changed: float | None = None
        self._fade_until = 0.0

    @property
    def condition(self) -> Condition:
        return (self.palette, self.rain, self.lightning)

    def reset(self) -> None:
        """The city emptied: the next game starts in the default weather, its program from the
        beginning (II §17)."""
        self.palette = policy.DEFAULT_PALETTE
        self.rain = 0
        self.lightning = 0
        self._due = None
        self._next = 0
        self._palette_changed = None
        self._fade_until = 0.0

    def start(self, now: float) -> None:
        """The city's game began: the program's first step is due ``after`` seconds from now."""
        if self._program:
            self._due = now + self._program[0].after
            self._next = 0

    # --- the Weather module ------------------------------------------------------------------

    def can_cycle(self, now: float) -> bool:
        """Whether the Weather module may change the palette now: not while a program's
        crossfade runs on the clients (I §8.20)."""
        return now >= self._fade_until

    def cycle(self, now: float) -> int:
        """The Weather module: the next palette, 0 -> 1 -> ... -> 4 -> 0 (I §9.3.5). The caller
        has checked ``can_cycle``."""
        self.palette = (self.palette + 1) % (limits.AMBIENT_PALETTE_MAX + 1)
        self._palette_changed = now
        return self.palette

    # --- the program -----------------------------------------------------------------------

    def tick(self, now: float) -> list[tuple[Condition, Condition]]:
        """Apply every step that is due: ``(before, after)`` for each. A step that would change the
        palette waits until the last palette change is ``CROSSFADE_SECONDS`` old; the steps behind
        it wait too (I §8.20)."""
        applied: list[tuple[Condition, Condition]] = []
        while self._due is not None and now >= self._due:
            step = self._program[self._next]
            changes_light = step.light is not None and step.light != self.palette
            if changes_light and not self._palette_free(now):
                break
            before = self.condition
            if step.light is not None:
                self.palette = step.light
            if step.rain is not None:
                self.rain = step.rain
            if step.lightning is not None:
                self.lightning = int(step.lightning)
            if changes_light:
                self._palette_changed = now
                self._fade_until = now + policy.CROSSFADE_SECONDS
            self._next = (self._next + 1) % len(self._program)
            self._due += self._program[self._next].after
            applied.append((before, self.condition))
        return applied

    def _palette_free(self, now: float) -> bool:
        return (
            self._palette_changed is None or now - self._palette_changed >= policy.CROSSFADE_SECONDS
        )

    # --- what a client is told (``CityState``) -----------------------------------------------

    @property
    def state_key(self) -> str:
        return WEATHER_KEY

    @property
    def state_value(self) -> Hashable:
        return self.condition

    def state_messages(self, now: float) -> Sequence[ServerMessage]:
        """The condition at once, as a fresh client needs it: ``'R'`` subs 0, 2 and 4 for each
        part that is not what such a client already shows (I §7.3, I §8.20)."""
        return self.messages_since(CLIENT_CONDITION, now)

    def messages_since(self, told: Hashable, now: float) -> Sequence[ServerMessage]:
        """The condition at once, for a client whose copy is ``told``: only what differs."""
        shown = told if isinstance(told, tuple) and len(told) == 3 else CLIENT_CONDITION
        return _messages(shown, self.condition, (_PALETTE_NOW, _RAIN_NOW, _LIGHTNING))

    def transition_messages(self, told: Condition) -> list[ServerMessage]:
        """A client that is present and shows ``told`` is moved to the condition gently: the palette
        by a crossfade (sub 1), the rain by a ramp (sub 3), the lightning at once (I §8.20)."""
        return _messages(told, self.condition, (_PALETTE_FADE, _RAIN_RAMP, _LIGHTNING))

    def announce(self, views: Iterable[ClientView], before: Condition, now: float) -> list[Event]:
        """The condition just moved on from ``before``: each client that showed it is sent the
        transition, any other the condition at once (it had missed an earlier change)."""
        events: list[Event] = []
        for view in views:
            if view.was_told(WEATHER_KEY, before):
                messages = self.transition_messages(before)
            else:
                told = view.told.get(WEATHER_KEY, CLIENT_CONDITION)
                messages = list(self.messages_since(told, now))
            view.tell(WEATHER_KEY, self.condition)
            events += [Event(m, ToPlayer(view.player_id)) for m in messages]
        return events
