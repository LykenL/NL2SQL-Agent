"""Shared pytest fixtures for the repository tests."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DB = REPO_ROOT / "examples" / "databases" / "company.db"
SETUP_SCRIPTS = REPO_ROOT / "examples" / "setup_scripts"
sys.path.insert(0, str(SETUP_SCRIPTS))

from db_setup import setup_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def ensure_sample_database() -> None:
    """Create the gitignored DB because SQLite capability tests need a real file on disk."""
    if not SAMPLE_DB.exists():
        SAMPLE_DB.parent.mkdir(parents=True, exist_ok=True)
        setup_database(f"sqlite:///{SAMPLE_DB}")
