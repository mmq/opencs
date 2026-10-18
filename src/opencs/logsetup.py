"""Apply ``[logging]`` to the standard ``logging`` module (DESIGN §6).

Everything the server logs goes through the ``opencs`` logger and its children, one per layer
(``opencs.net``, ``opencs.session``, ...). ``configure_logging`` gives that tree a stderr handler
and, if configured, a rotating file, and sets the default level and any per-layer level. It owns
the ``opencs`` logger's handlers: calling it again replaces what it installed before.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from opencs.config import LoggingConfig

ROOT_LOGGER = "opencs"

#: Marks the handlers this module installed, so a second call can remove exactly those.
_OWNED = "_opencs_owned"


def configure_logging(config: LoggingConfig) -> None:
    """Install the handlers and levels ``config`` names on the ``opencs`` logger tree."""
    root = logging.getLogger(ROOT_LOGGER)
    reset_logging()
    formatter = logging.Formatter(config.format)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if config.file is not None:
        config.file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                config.file, maxBytes=config.max_bytes, backupCount=config.backups, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _OWNED, True)
        root.addHandler(handler)
    root.setLevel(config.level)
    root.propagate = False  # the server's lines are its own; the host's root logger is not used
    for layer, level in config.levels:
        logging.getLogger(f"{ROOT_LOGGER}.{layer}").setLevel(level)


def reset_logging() -> None:
    """Remove what ``configure_logging`` installed and the per-layer levels (for tests, and for
    a second call)."""
    root = logging.getLogger(ROOT_LOGGER)
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.NOTSET)
    root.propagate = True
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(ROOT_LOGGER + "."):
            logging.getLogger(name).setLevel(logging.NOTSET)
