"""What an operator can do to the running server (DESIGN §6): the services behind the admin
channel and the signal handlers.

Everything here runs on the event loop and is synchronous, like the world: it reads and changes
the same objects the sessions do, so no lock is needed. It speaks in plain text lines that the
admin channel (the I/O) writes out.
"""

from __future__ import annotations

import logging

from opencs.lobby import replies
from opencs.messages import server
from opencs.routing import Router
from opencs.session.phases import Services
from opencs.world import chat

log = logging.getLogger("opencs.admin")

#: The notice players get when the server is going down (ours).
SHUTDOWN_NOTICE = "SERVER SHUTTING DOWN"


def announce(services: Services, text: str, router: Router | None = None) -> int:
    """Put ``text`` on every player's screen: an ``'a'`` from sender 100 (I §8.23) to each client
    in a game that has started playing, a plain line to each pilot in the Chamber. The text is
    cut to what the client accepts and to printable ASCII. Returns how many received it."""
    router = router or services.router
    clean = server.sanitize_chat(text.encode("ascii", "replace"))
    received = 0
    for city in services.cities.values():
        audience = city.playing()
        router.deliver(city, [chat.system_line(clean.decode("ascii"), audience)])
        received += len(router.resolve(city, audience))
    line = replies.render([replies.body(f"*** {clean.decode('ascii')}")])
    for member in services.lobby.chamber():
        member.deliver(line)
        received += 1
    return received


def remove_everyone(services: Services, notice: str) -> int:
    """Shutdown: end every session, a game with the unprompted ``'C'`` and a leave that saves the
    player (``Session.remove``), anything else with a line and a close. Returns how many."""
    sessions = services.sessions.all()
    for session in sessions:
        try:
            session.remove(notice, close=True)
        except Exception:
            # One session failing must not keep the others from being saved and closed.
            log.exception("could not remove a session at shutdown")
    return len(sessions)
