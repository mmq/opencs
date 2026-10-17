"""Chat and HUD text (I §8.23, I App. B).

The client shows nothing it types until the server sends it back as ``'a'``, so every line, the
typist's own included, comes from here. The same message is the server's only way to put a line on
a player's screen, which ``system_line`` serves for every later milestone.
"""

from __future__ import annotations

from opencs.messages import client, server
from opencs.messages.limits import CHAT_STYLE_OPEN, CHAT_STYLE_TEAM, SYSTEM_SENDER
from opencs.world.events import Audience, Event, ToAll, ToTeam


def chat_line(sender_id: int, team: int, typed: client.Chat) -> Event | None:
    """The ``'a'`` for a line a player typed: ``sender`` is the typist's pod id (the client adds the
    callsign), the style is the one it sent, and the audience is every client for the open channel
    or the typist's team for the team channel; the typist is in both, as it does not draw its own
    line otherwise. Text is cut to printable ASCII, 230 bytes. A style the client never sends is
    dropped (None): we relay chat styles 0 and 1 only.
    """
    if typed.style == CHAT_STYLE_OPEN:
        audience: Audience = ToAll()
    elif typed.style == CHAT_STYLE_TEAM:
        audience = ToTeam(team)
    else:
        return None
    text = server.sanitize_chat(typed.text)
    return Event(server.Chat(sender_id, typed.style, text), audience)


def system_line(text: str, audience: Audience | None = None) -> Event:
    """A system line (``- text``, sender 100) on the screens of ``audience``, every client by
    default. Non-printable characters become spaces and the line is cut to 230 bytes, as for any
    chat."""
    clean = server.sanitize_chat(text.encode("ascii", "replace"))
    return Event(server.Chat(SYSTEM_SENDER, CHAT_STYLE_OPEN, clean), audience or ToAll())
