"""Shared fixtures."""

from collections.abc import Iterator

import pytest

from opencs.logsetup import reset_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """``main`` configures the ``opencs`` logger tree; no test may leave that behind."""
    yield
    reset_logging()
