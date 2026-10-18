"""Per-connection log context: the id and callsign every record carries (DESIGN §6)."""

from __future__ import annotations

import logging
from typing import Any


class ConnectionContext:
    """What identifies a connection in a log: its id, and its callsign once the player is known.

    Shared by the net and session layers' loggers, so a callsign learned in the session shows in
    the net layer's lines too.
    """

    def __init__(self, conn_id: int, address: str = "?") -> None:
        self.conn_id = conn_id
        #: The peer's IP address, which the session layer limits what one source may hold by.
        self.address = address
        self.callsign = "-"


class ConnectionLog(logging.LoggerAdapter[logging.Logger]):
    """``[conn=3 ALPHA] message``; the callsign is ``-`` until the player is known."""

    def __init__(self, logger: logging.Logger, context: ConnectionContext) -> None:
        super().__init__(logger, {})
        self.context = context

    @property
    def callsign(self) -> str:
        return self.context.callsign

    @callsign.setter
    def callsign(self, value: str) -> None:
        self.context.callsign = value

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return f"[conn={self.context.conn_id} {self.context.callsign}] {msg}", kwargs
