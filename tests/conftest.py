"""Shared fixtures for obsidian-agent tests."""
import pytest
from pathlib import Path

import agent
import daily_agent
import incremental_agent


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal PARA vault directory structure under tmp_path."""
    (tmp_path / "Daily Notes").mkdir()
    (tmp_path / "Daily Reviews").mkdir()
    (tmp_path / "3-Resources" / "Weekly Reviews").mkdir(parents=True)
    (tmp_path / "1-Projects").mkdir()
    (tmp_path / "2-Areas").mkdir()
    (tmp_path / "4-Archive").mkdir()
    return tmp_path


@pytest.fixture
def patched_weekly(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch agent.py module globals to point at the tmp vault."""
    monkeypatch.setattr(agent, "VAULT_DIR", vault)
    monkeypatch.setattr(agent, "DRY_RUN", False)
    monkeypatch.setattr(agent, "WEEK", "2026-W15")
    return vault


@pytest.fixture
def patched_daily(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch daily_agent.py module globals to point at the tmp vault."""
    monkeypatch.setattr(daily_agent, "VAULT_DIR", vault)
    monkeypatch.setattr(daily_agent, "DRY_RUN", False)
    monkeypatch.setattr(daily_agent, "DATE", "2026-04-12")
    return vault


@pytest.fixture
def patched_incremental(vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch incremental_agent.py module globals to point at the tmp vault."""
    monkeypatch.setattr(incremental_agent, "VAULT_DIR", vault)
    monkeypatch.setattr(incremental_agent, "DRY_RUN", False)
    monkeypatch.setattr(incremental_agent, "BATCH_MODE", False)
    monkeypatch.setattr(
        incremental_agent, "STATE_FILE", vault / ".obsidian-agent-state.json"
    )
    return vault
