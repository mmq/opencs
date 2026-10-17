"""The Chamber: the lobby as a text session (I §5.2-§5.5, II §4)."""

from __future__ import annotations

from opencs.session.phases import TextPhase
from opencs.wire.text import arm_line


class ChamberPhase(TextPhase):
    """Placeholder until the lobby commands are built: only the version-check request."""

    def enter(self) -> None:
        self.host.join_lobby()
        self.host.io.send_raw(arm_line("PRESS RETURN"))

    def on_line(self, line: str) -> None:
        pass
