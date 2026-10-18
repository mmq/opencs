"""The README's links and the documentation map stay true."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()


def test_every_relative_link_in_the_readme_resolves() -> None:
    links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", README)
    assert len(links) >= 14
    missing = [x for x in links if not x.startswith("http") and not (ROOT / x).exists()]
    assert missing == []


def test_every_guide_in_docs_is_on_the_documentation_map() -> None:
    guides = [
        "PLAYING",
        "DEPLOYMENT",
        "operations",
        "robustness",
        "DESIGN",
    ]
    assert [g for g in guides if f"docs/{g}.md" not in README] == []
