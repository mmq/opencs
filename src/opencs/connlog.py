"""A logger adapter that stamps every record with the connection id and callsign (DESIGN §6)."""

from __future__ import annotations

import logging
from typing import Any


class ConnectionLog(logging.LoggerAdapter[logging.Logger]):
    """``[conn=3 ALPHA] message``; the callsign is ``-`` until the player is known."""

    def __init__(self, logger: logging.Logger, conn_id: int) -> None:
        super().__init__(logger, {})
        self.conn_id = conn_id
        self.callsign = "-"

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return f"[conn={self.conn_id} {self.callsign}] {msg}", kwargs
