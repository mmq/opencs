"""Layering guard (DESIGN §3): a package imports only from layers below it."""

from __future__ import annotations

import ast
from pathlib import Path

import opencs

ROOT = Path(opencs.__file__).parent

#: package -> the ``opencs`` modules/packages it may import (besides itself).
ALLOWED: dict[str, set[str]] = {
    "wire": {"policy"},
    "messages": {"wire", "policy"},
    "store": {"policy"},
    "lobby": {"wire", "policy", "config", "store"},
    "session": {"wire", "messages", "lobby", "store", "policy", "config", "clock", "connlog"},
    "net": {"wire", "messages", "policy", "clock", "connlog"},
    "world": {"wire", "messages", "policy", "config"},
}


#: Packages that must be sans-IO (DESIGN §3, §3.4) and the standard-library modules they may not
#: import: no sockets, no event loop, no threads, no wall clock.
SANS_IO = ("session", "lobby", "world")
FORBIDDEN_STDLIB = {"asyncio", "socket", "selectors", "threading", "time", "select", "ssl"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path}: use absolute imports")
            base = node.module or ""
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}
    return found


def test_packages_import_only_allowed_layers() -> None:
    for package, allowed in ALLOWED.items():
        for path in (ROOT / package).rglob("*.py"):
            for name in _imports(path):
                parts = name.split(".")
                if parts[0] != "opencs" or len(parts) == 1:
                    continue
                top = parts[1]
                assert top == package or top in allowed, f"{path.name} imports {name}"


def test_the_guard_covers_the_layers_the_design_forbids() -> None:
    # net knows nothing of session, lobby or world; session nothing of net; lobby of neither.
    assert not {"session", "lobby", "world"} & ALLOWED["net"]
    assert "net" not in ALLOWED["session"]
    assert not {"net", "session"} & ALLOWED["lobby"]
    assert not {"net", "session", "lobby", "routing", "asyncio"} & ALLOWED["world"]
    assert not {"net", "session", "lobby", "world"} & ALLOWED["wire"]
    assert not {"net", "session", "lobby", "world"} & ALLOWED["messages"]


def test_sans_io_packages_import_no_io_modules() -> None:
    for package in SANS_IO:
        for path in (ROOT / package).rglob("*.py"):
            for name in _imports(path):
                assert name.split(".")[0] not in FORBIDDEN_STDLIB, f"{path.name} imports {name}"
