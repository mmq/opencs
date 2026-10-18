"""``[logging]`` applied to the ``opencs`` logger tree (DESIGN §6), and passwords kept out of it."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import opencs
from fixtures.sessions import connect, make_world
from opencs import policy
from opencs.config import LoggingConfig
from opencs.logsetup import configure_logging, reset_logging


def test_levels_per_layer_and_a_file_handler(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "server.log"
    configure_logging(
        LoggingConfig(level="WARNING", levels=(("net", "DEBUG"), ("world", "ERROR")), file=path)
    )
    logging.getLogger("opencs.session").info("session info")  # under the default WARNING
    logging.getLogger("opencs.session").warning("session warning")
    logging.getLogger("opencs.net").debug("net debug")  # the layer's own level lets it by
    logging.getLogger("opencs.world").warning("world warning")  # the layer is stricter
    logging.getLogger("opencs.world.grace").error("world child error")  # a child inherits
    for handler in logging.getLogger("opencs").handlers:
        handler.flush()
    lines = path.read_text().splitlines()
    assert [line.split(": ", 1)[1] for line in lines] == [
        "session warning",
        "net debug",
        "world child error",
    ]
    assert "WARNING opencs.session" in lines[0]


def test_the_format_is_applied(tmp_path: Path) -> None:
    path = tmp_path / "f.log"
    configure_logging(LoggingConfig(format="%(levelname)s|%(name)s|%(message)s", file=path))
    logging.getLogger("opencs.lobby").info("hello")
    for handler in logging.getLogger("opencs").handlers:
        handler.flush()
    assert path.read_text() == "INFO|opencs.lobby|hello\n"


def test_the_file_rotates_at_its_cap(tmp_path: Path) -> None:
    path = tmp_path / "r.log"
    configure_logging(LoggingConfig(file=path, max_bytes=200, backups=1, format="%(message)s"))
    for n in range(30):
        logging.getLogger("opencs.app").info("line %02d %s", n, "x" * 20)
    for handler in logging.getLogger("opencs").handlers:
        handler.flush()
    assert (tmp_path / "r.log.1").exists()
    assert not (tmp_path / "r.log.2").exists()
    assert path.stat().st_size <= 200


def test_configuring_twice_replaces_the_handlers(tmp_path: Path) -> None:
    configure_logging(LoggingConfig(file=tmp_path / "a.log"))
    configure_logging(LoggingConfig(file=tmp_path / "b.log"))
    assert len(logging.getLogger("opencs").handlers) == 2  # stderr and one file
    reset_logging()
    assert logging.getLogger("opencs").handlers == []


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.setFormatter(logging.Formatter("%(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.text.append(self.format(record))
        if record.args:
            self.text.append(repr(record.args))


def test_a_password_never_reaches_a_log_record() -> None:
    """The account line is ``NAME,PASSWORD`` (I §5.1): a new account, a right password and a
    wrong one, at the most verbose level, in every logger the server uses."""
    collect = _Collect()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(collect)
    root.setLevel(logging.DEBUG)
    try:
        world = make_world()
        for line in ("NEWPILOT,hunter2xyz", "NEWPILOT,wrongxyz9", "NEWPILOT,hunter2xyz"):
            h = connect(world)
            h.feed(f"HHH\r{line}\r<Not Telling!>\rM 1380;3\r")
            h.session.closed()
    finally:
        root.removeHandler(collect)
        root.setLevel(previous)
    joined = "\n".join(collect.text)
    assert "NEWPILOT" in joined  # the lines were captured at all
    assert "hunter2xyz" not in joined and "wrongxyz9" not in joined


def test_every_logger_in_the_source_is_a_layer_logger() -> None:
    """One logger per layer (DESIGN §6): a stray ``getLogger(__name__)`` or a made-up name would
    escape the per-layer levels."""
    root = Path(opencs.__file__).parent
    names = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
                and node.args
                and path.name != "logsetup.py"
            ):
                arg = node.args[0]
                assert isinstance(arg, ast.Constant), f"{path.name}: logger name is not a literal"
                names.append((path.name, arg.value))
    assert len(names) > 10
    for file, name in names:
        parts = str(name).split(".")
        assert parts[0] == "opencs" and parts[1] in policy.LOG_LAYERS, f"{file}: {name}"
