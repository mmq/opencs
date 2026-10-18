"""The repository's public face (DESIGN §10): the game's files never enter it, and the project is
never named after the game. The name may appear in prose only as a statement of compatibility."""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
GAME_SUFFIXES = {".fun", ".cbd", ".crs", ".grs"}
GAME_NAMES = {"cs.cfg"}
NAME = re.compile(r"cyber\s*strike", re.IGNORECASE)


def _tracked() -> list[Path] | None:
    """The files git tracks, or None outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(ROOT / name for name in out.decode().split("\0") if name)


def repo_files() -> Iterator[Path]:
    """Tracked files; without git, every file but the tool caches and the ignored root ``/data``."""
    tracked = _tracked()
    if tracked is not None:
        yield from (p for p in tracked if p.is_file())
        return
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if SKIPPED_DIRS & set(rel.parts) or rel.parts[0] == "data" or not path.is_file():
            continue
        yield path


def python_files() -> list[Path]:
    return [p for p in repo_files() if p.suffix == ".py"]


def test_no_game_file_is_in_the_repository() -> None:
    found = [
        str(p.relative_to(ROOT))
        for p in repo_files()
        if p.suffix.lower() in GAME_SUFFIXES or p.name.lower() in GAME_NAMES
    ]
    assert found == []


def test_gitignore_keeps_game_files_state_and_captures_out() -> None:
    lines = set((ROOT / ".gitignore").read_text().splitlines())
    assert {"/data/", "/captures/", "CYBER/", "*.FUN", "*.CBD", "*.CRS", "*.GRS", "*.CFG"} <= lines


def test_no_python_identifier_is_named_after_the_game() -> None:
    bad: list[str] = []
    for path in python_files():
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, ast.alias):
                names.append(node.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            bad += [f"{path.relative_to(ROOT)}: {n}" for n in names if NAME.search(n)]
    assert bad == []


def test_no_path_or_package_is_named_after_the_game() -> None:
    assert [str(p) for p in repo_files() if NAME.search(str(p.relative_to(ROOT)))] == []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "opencs"
    assert not NAME.search(project["name"])
    assert not (ROOT / "src" / "cyberstrike").exists()


def test_no_document_is_titled_with_the_game_name() -> None:
    docs = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    bad = []
    for path in docs:
        title = next((x for x in path.read_text().splitlines() if x.startswith("# ")), "")
        if NAME.search(title):
            bad.append(f"{path.relative_to(ROOT)}: {title}")
    assert bad == []


def test_the_name_is_only_a_statement_of_compatibility_in_package_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["description"].startswith("A server compatible with the 1993 online game")
    first = (ROOT / "src/opencs/__init__.py").read_text().splitlines()[0]
    assert "compatible with" in first


def test_the_name_does_not_lead_a_title_or_a_command() -> None:
    """The mutation this guards: a log banner, a --help text or a service named after the game."""
    banner_sources = [p for p in python_files() if "src" in p.parts]
    for path in banner_sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and NAME.match(node.value.strip())
            ):
                raise AssertionError(f"{path.relative_to(ROOT)}: {node.value[:40]!r}")
