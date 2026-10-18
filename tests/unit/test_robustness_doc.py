"""Every test that docs/robustness.md names exists, so the audit table cannot rot."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = re.compile(r"`([\w/]+)::(test_\w+)`")


def test_every_test_the_audit_names_exists() -> None:
    text = (ROOT / "docs" / "robustness.md").read_text()
    missing = []
    found = REFERENCE.findall(text)
    assert len(found) > 25
    for module, name in found:
        candidates = [ROOT / "tests" / "unit" / f"{module}.py", ROOT / "tests" / f"{module}.py"]
        source = next((p.read_text() for p in candidates if p.exists()), None)
        if source is None or f"def {name}(" not in source:
            missing.append(f"{module}::{name}")
    assert not missing, missing
