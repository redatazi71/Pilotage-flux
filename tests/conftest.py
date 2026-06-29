"""Fixtures pytest communes."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.db import init_schema, connect


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Cree une base SQLite vide initialisee avec le schema V0."""
    return init_schema(tmp_path / "test.db", drop_existing=True)


@pytest.fixture
def fixtures_dir() -> Path:
    """Chemin vers data/fixtures/ depuis la racine projet."""
    here = Path(__file__).resolve().parent
    root = here.parent
    return root / "data" / "fixtures"
