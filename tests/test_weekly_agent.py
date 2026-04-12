"""Tests for agent.py (weekly review)."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import agent


# ---------------------------------------------------------------------------
# _extract_summary
# ---------------------------------------------------------------------------

class TestExtractSummary:
    def test_extracts_text_after_summary_marker(self):
        text = "Done all the work.\n\nSUMMARY: This week was productive."
        assert agent._extract_summary(text) == "This week was productive."

    def test_returns_stripped_full_text_when_no_marker(self):
        text = "  Just a plain response.  "
        assert agent._extract_summary(text) == "Just a plain response."

    def test_empty_input_returns_empty_string(self):
        assert agent._extract_summary("") == ""

    def test_summary_marker_at_start(self):
        text = "SUMMARY: Short summary."
        assert agent._extract_summary(text) == "Short summary."

    def test_strips_whitespace_around_summary(self):
        text = "Intro.\n\nSUMMARY:\n  Indented summary text.\n"
        assert agent._extract_summary(text) == "Indented summary text."

    def test_uses_first_occurrence_of_marker(self):
        text = "SUMMARY: First.\n\nSUMMARY: Second."
        assert agent._extract_summary(text) == "First.\n\nSUMMARY: Second."


# ---------------------------------------------------------------------------
# _is_safe_write_path
# ---------------------------------------------------------------------------

class TestIsSafeWritePath:
    @pytest.mark.parametrize("path,expected", [
        ("Daily Notes/2026-01-01.md", True),
        ("3-Resources/Weekly Reviews/2026-W01.md", True),
        ("Home.md", True),
        # Exact prefix match without trailing slash
        ("Daily Notes", True),
        ("3-Resources/Weekly Reviews", True),
        # Blocked paths
        ("4-Archive/old.md", False),
        ("1-Projects/project.md", False),
        ("random.md", False),
        ("", False),
        # Adjacent prefix — must NOT match
        ("Daily Notes-evil/file.md", False),
        ("3-Resources/Weekly Reviews-evil/file.md", False),
        ("../etc/passwd", False),
    ])
    def test_path_allowlist(self, path, expected):
        assert agent._is_safe_write_path(path) is expected


# ---------------------------------------------------------------------------
# _resolve_vault_path
# ---------------------------------------------------------------------------

class TestResolveVaultPath:
    def test_normal_relative_path_resolves(self, patched_weekly):
        vault = patched_weekly
        (vault / "Daily Notes" / "note.md").write_text("content")
        result = agent._resolve_vault_path("Daily Notes/note.md")
        assert result == vault / "Daily Notes" / "note.md"

    def test_dotdot_traversal_raises(self, patched_weekly):
        with pytest.raises(ValueError, match="escapes vault root"):
            agent._resolve_vault_path("../etc/passwd")

    def test_deep_traversal_raises(self, patched_weekly):
        with pytest.raises(ValueError, match="escapes vault root"):
            agent._resolve_vault_path("subdir/../../etc/passwd")

    @pytest.mark.xfail(
        strict=True,
        reason="Bug 3: str.startswith(str(VAULT_DIR)) allows adjacent dirs like vault-evil/",
    )
    def test_adjacent_dir_bypass_BUG3(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        evil = tmp_path / "vault-evil"
        evil.mkdir()
        (evil / "secret.md").write_text("secret")
        monkeypatch.setattr(agent, "VAULT_DIR", vault)
        with pytest.raises(ValueError, match="escapes vault root"):
            agent._resolve_vault_path(str(evil / "secret.md"))


# ---------------------------------------------------------------------------
# File I/O helpers (require patched_weekly)
# ---------------------------------------------------------------------------

class TestFileOperations:
    def test_list_files_returns_sorted_names(self, patched_weekly):
        vault = patched_weekly
        (vault / "Daily Notes" / "b.md").write_text("")
        (vault / "Daily Notes" / "a.md").write_text("")
        result = agent.list_files("Daily Notes")
        assert result == "a.md\nb.md"

    def test_list_files_missing_dir_returns_error(self, patched_weekly):
        result = agent.list_files("nonexistent")
        assert "does not exist" in result

    def test_list_files_empty_dir_returns_marker(self, patched_weekly):
        result = agent.list_files("Daily Notes")
        assert "(empty directory)" in result

    def test_read_file_returns_content(self, patched_weekly):
        vault = patched_weekly
        (vault / "Daily Notes" / "2026-04-07.md").write_text("Hello week")
        assert agent.read_file("Daily Notes/2026-04-07.md") == "Hello week"

    def test_read_file_missing_returns_error(self, patched_weekly):
        result = agent.read_file("Daily Notes/missing.md")
        assert "File not found" in result

    def test_write_file_allowed_path_writes_content(self, patched_weekly):
        vault = patched_weekly
        result = agent.write_file("Daily Notes/note.md", "hello")
        assert "Written" in result
        assert (vault / "Daily Notes" / "note.md").read_text() == "hello"

    def test_write_file_creates_parent_dirs(self, patched_weekly):
        vault = patched_weekly
        agent.write_file("3-Resources/Weekly Reviews/2026-W15.md", "review")
        assert (vault / "3-Resources" / "Weekly Reviews" / "2026-W15.md").exists()

    def test_write_file_blocked_path_returns_rejection(self, patched_weekly):
        result = agent.write_file("4-Archive/bad.md", "content")
        assert "rejected" in result.lower()

    def test_write_file_dry_run_skips_write(self, patched_weekly, monkeypatch):
        vault = patched_weekly
        monkeypatch.setattr(agent, "DRY_RUN", True)
        result = agent.write_file("Daily Notes/note.md", "hello")
        assert "DRY_RUN" in result
        assert not (vault / "Daily Notes" / "note.md").exists()

    def test_append_to_file_appends_content(self, patched_weekly):
        vault = patched_weekly
        p = vault / "Daily Notes" / "note.md"
        p.write_text("Line 1\n")
        agent.append_to_file("Daily Notes/note.md", "Line 2\n")
        assert p.read_text() == "Line 1\nLine 2\n"

    def test_append_to_file_dry_run_skips(self, patched_weekly, monkeypatch):
        vault = patched_weekly
        monkeypatch.setattr(agent, "DRY_RUN", True)
        p = vault / "Daily Notes" / "note.md"
        p.write_text("Original\n")
        result = agent.append_to_file("Daily Notes/note.md", "Extra\n")
        assert "DRY_RUN" in result
        assert p.read_text() == "Original\n"


# ---------------------------------------------------------------------------
# dispatch_tool
# ---------------------------------------------------------------------------

class TestDispatchTool:
    def test_routes_list_files(self, patched_weekly):
        result = agent.dispatch_tool("list_files", {"subdir": "Daily Notes"})
        assert "(empty directory)" in result

    def test_routes_read_file(self, patched_weekly):
        vault = patched_weekly
        (vault / "Daily Notes" / "note.md").write_text("content")
        result = agent.dispatch_tool("read_file", {"path": "Daily Notes/note.md"})
        assert result == "content"

    def test_routes_write_file(self, patched_weekly):
        result = agent.dispatch_tool(
            "write_file", {"path": "Daily Notes/x.md", "content": "hi"}
        )
        assert "Written" in result

    def test_routes_append_to_file(self, patched_weekly):
        vault = patched_weekly
        (vault / "Daily Notes" / "x.md").write_text("a\n")
        result = agent.dispatch_tool(
            "append_to_file", {"path": "Daily Notes/x.md", "content": "b\n"}
        )
        assert "Appended" in result

    def test_unknown_tool_returns_error(self, patched_weekly):
        result = agent.dispatch_tool("not_a_tool", {})
        assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# Agentic loop (run_agent) — mocked Anthropic client
# ---------------------------------------------------------------------------

def _make_end_turn_response(summary_text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = f"Analysis complete.\n\nSUMMARY: {summary_text}"
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_tool_use_response(tool_name: str = "read_file", tool_id: str = "tu_1") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = {"path": "Daily Notes/note.md"}
    block.id = tool_id
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


class TestWeeklyAgentLoop:
    def test_single_turn_returns_summary(self, patched_weekly, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_end_turn_response("Great week.")
        mocker.patch("agent.anthropic.Anthropic", return_value=mock_client)
        result = agent.run_agent()
        assert result == "Great week."
        assert mock_client.messages.create.call_count == 1

    def test_tool_use_then_end_turn(self, patched_weekly, mocker):
        vault = patched_weekly
        (vault / "Daily Notes" / "note.md").write_text("# Note")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _make_tool_use_response("read_file", "tu_1"),
            _make_end_turn_response("Used a tool."),
        ]
        mocker.patch("agent.anthropic.Anthropic", return_value=mock_client)
        result = agent.run_agent()
        assert result == "Used a tool."
        assert mock_client.messages.create.call_count == 2

    def test_unexpected_stop_reason_raises(self, patched_weekly, mocker):
        resp = MagicMock()
        resp.stop_reason = "max_tokens"
        resp.content = []
        mock_client = MagicMock()
        mock_client.messages.create.return_value = resp
        mocker.patch("agent.anthropic.Anthropic", return_value=mock_client)
        with pytest.raises(RuntimeError, match="Unexpected stop_reason"):
            agent.run_agent()
