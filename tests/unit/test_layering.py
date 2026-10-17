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
}


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
