"""Shared pytest fixtures.

The TestClient runs against the real `api.main:app`, which reads from the
on-disk SQLite files under `data/`. Tests are smoke tests, not isolated unit
tests — they assume the data files exist (the same prerequisite as `uvicorn`).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    """Ensure individual test overrides don't leak into the next test."""
    yield
    app.dependency_overrides.clear()
