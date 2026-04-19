"""Tests for daily_agent.py."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import daily_agent


# ---------------------------------------------------------------------------
# _extract_summary
# ---------------------------------------------------------------------------

class TestExtractSummary:
    def test_extracts_text_after_summary_marker(self):
        text = "Done for today.\n\nSUMMARY: Found 3 action items."
        assert daily_agent._extract_summary(text) == "Found 3 action items."

    def test_returns_stripped_full_text_when_no_marker(self):
        text = "  Plain response.  "
        assert daily_agent._extract_summary(text) == "Plain response."

    def test_empty_input_returns_empty_string(self):
        assert daily_agent._extract_summary("") == ""

    def test_summary_at_start(self):
        text = "SUMMARY: Nothing happened."
        assert daily_agent._extract_summary(text) == "Nothing happened."

    def test_strips_whitespace_around_summary(self):
        text = "Preamble.\n\nSUMMARY:\n  Indented text.\n"
        assert daily_agent._extract_summary(text) == "Indented text."


# ---------------------------------------------------------------------------
# _is_safe_write_path
# ---------------------------------------------------------------------------

class TestIsSafeWritePath:
    @pytest.mark.parametrize("path,expected", [
        ("Daily Reviews/2026-04-12.md", True),
        ("Daily Reviews", True),   # exact prefix match without slash
        # Blocked paths — daily agent only allows Daily Reviews/
        ("Daily Notes/2026-04-12.md", False),
        ("Home.md", False),
        ("3-Resources/refs.md", False),
        ("random.md", False),
        ("", False),
        ("Daily Reviews-evil/file.md", False),
        ("../etc/passwd", False),
    ])
    def test_path_allowlist(self, path, expected):
        assert daily_agent._is_safe_write_path(path) is expected


# ---------------------------------------------------------------------------
# _resolve_vault_path
# NOTE: daily_agent correctly uses Path.is_relative_to() — no Bug 3 here.
# ---------------------------------------------------------------------------

class TestResolveVaultPath:
    def test_normal_relative_path_resolves(self, patched_daily):
        vault = patched_daily
        (vault / "Daily Reviews" / "note.md").write_text("content")
        result = daily_agent._resolve_vault_path("Daily Reviews/note.md")
        assert result == vault / "Daily Reviews" / "note.md"

    def test_dotdot_traversal_raises(self, patched_daily):
        with pytest.raises(ValueError, match="escapes vault root"):
            daily_agent._resolve_vault_path("../etc/passwd")

    def test_deep_traversal_raises(self, patched_daily):
        with pytest.raises(ValueError, match="escapes vault root"):
            daily_agent._resolve_vault_path("subdir/../../etc/passwd")

    def test_adjacent_dir_correctly_blocked(self, tmp_path, monkeypatch):
        """daily_agent uses is_relative_to() so adjacent directories are correctly blocked."""
        vault = tmp_path / "vault"
        vault.mkdir()
        evil = tmp_path / "vault-evil"
        evil.mkdir()
        (evil / "secret.md").write_text("secret")
        monkeypatch.setattr(daily_agent, "VAULT_DIR", vault)
        with pytest.raises(ValueError, match="escapes vault root"):
            daily_agent._resolve_vault_path(str(evil / "secret.md"))


# ---------------------------------------------------------------------------
# git_diff — subcommand allowlist
# ---------------------------------------------------------------------------

class TestGitDiff:
    def test_allowed_subcommand_status_calls_subprocess(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="M file.md\n", stderr="", returncode=0)
        result = daily_agent.git_diff("status --short")
        assert "M file.md" in result
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "git"
        assert call_args[1] == "status"

    def test_allowed_subcommand_diff(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="diff output\n", stderr="", returncode=0)
        result = daily_agent.git_diff("diff HEAD~1 HEAD")
        assert "diff output" in result

    def test_allowed_subcommand_log(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="abc123 commit\n", stderr="", returncode=0)
        result = daily_agent.git_diff("log --oneline -5")
        assert "abc123" in result

    def test_allowed_subcommand_show(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="show output\n", stderr="", returncode=0)
        result = daily_agent.git_diff("show HEAD")
        assert "show output" in result

    def test_allowed_subcommand_ls_files(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="file.md\n", stderr="", returncode=0)
        result = daily_agent.git_diff("ls-files")
        assert "file.md" in result

    def test_blocked_subcommand_commit(self, patched_daily):
        result = daily_agent.git_diff("commit -m bad")
        assert "not allowed" in result
        assert "commit" in result

    def test_blocked_subcommand_rm(self, patched_daily):
        result = daily_agent.git_diff("rm -rf .")
        assert "not allowed" in result

    def test_blocked_subcommand_push(self, patched_daily):
        result = daily_agent.git_diff("push origin main")
        assert "not allowed" in result

    def test_empty_args_returns_error(self, patched_daily):
        result = daily_agent.git_diff("")
        assert "no git arguments provided" in result

    def test_empty_subprocess_output_returns_marker(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        result = daily_agent.git_diff("status")
        assert result == "(no output)"

    def test_timeout_returns_error_message(self, patched_daily, mocker):
        import subprocess
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = daily_agent.git_diff("log --oneline")
        assert "timed out" in result


# ---------------------------------------------------------------------------
# File I/O helpers (require patched_daily)
# ---------------------------------------------------------------------------

class TestFileOperations:
    def test_list_files_returns_sorted_names(self, patched_daily):
        vault = patched_daily
        (vault / "Daily Notes" / "b.md").write_text("")
        (vault / "Daily Notes" / "a.md").write_text("")
        result = daily_agent.list_files("Daily Notes")
        assert result == "a.md\nb.md"

    def test_list_files_missing_dir_returns_error(self, patched_daily):
        result = daily_agent.list_files("nonexistent")
        assert "does not exist" in result

    def test_list_files_empty_dir_returns_marker(self, patched_daily):
        result = daily_agent.list_files("Daily Notes")
        assert "(empty directory)" in result

    def test_read_file_returns_content(self, patched_daily):
        vault = patched_daily
        (vault / "Daily Notes" / "2026-04-12.md").write_text("Today's note")
        assert daily_agent.read_file("Daily Notes/2026-04-12.md") == "Today's note"

    def test_read_file_missing_returns_error(self, patched_daily):
        result = daily_agent.read_file("Daily Notes/missing.md")
        assert "File not found" in result

    def test_write_file_allowed_path_writes_content(self, patched_daily):
        vault = patched_daily
        result = daily_agent.write_file("Daily Reviews/2026-04-12.md", "review")
        assert "Written" in result
        assert (vault / "Daily Reviews" / "2026-04-12.md").read_text() == "review"

    def test_write_file_blocked_path_returns_rejection(self, patched_daily):
        result = daily_agent.write_file("Daily Notes/note.md", "sneaky")
        assert "rejected" in result.lower()

    def test_write_file_dry_run_skips_write(self, patched_daily, monkeypatch):
        vault = patched_daily
        monkeypatch.setattr(daily_agent, "DRY_RUN", True)
        result = daily_agent.write_file("Daily Reviews/2026-04-12.md", "review")
        assert "DRY_RUN" in result
        assert not (vault / "Daily Reviews" / "2026-04-12.md").exists()


# ---------------------------------------------------------------------------
# dispatch_tool
# ---------------------------------------------------------------------------

class TestDispatchTool:
    def test_routes_list_files(self, patched_daily):
        result = daily_agent.dispatch_tool("list_files", {"subdir": "Daily Notes"})
        assert "(empty directory)" in result

    def test_routes_read_file(self, patched_daily):
        vault = patched_daily
        (vault / "Daily Notes" / "note.md").write_text("content")
        result = daily_agent.dispatch_tool("read_file", {"path": "Daily Notes/note.md"})
        assert result == "content"

    def test_routes_write_file(self, patched_daily):
        result = daily_agent.dispatch_tool(
            "write_file", {"path": "Daily Reviews/note.md", "content": "hi"}
        )
        assert "Written" in result

    def test_routes_git_diff(self, patched_daily, mocker):
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="clean\n", stderr="", returncode=0)
        result = daily_agent.dispatch_tool("git_diff", {"args": "status"})
        assert "clean" in result

    def test_unknown_tool_returns_error(self, patched_daily):
        result = daily_agent.dispatch_tool("not_a_tool", {})
        assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# Agentic loop (run_agent) — mocked Anthropic client
# ---------------------------------------------------------------------------

def _make_end_turn_response(summary_text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = f"Daily review done.\n\nSUMMARY: {summary_text}"
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_tool_use_response(tool_name: str = "read_file", tool_id: str = "tu_1") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = {"args": "status"}
    block.id = tool_id
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


class TestDailyAgentLoop:
    def test_single_turn_returns_summary(self, patched_daily, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_end_turn_response("No changes today.")
        mocker.patch("daily_agent.anthropic.Anthropic", return_value=mock_client)
        result = daily_agent.run_agent()
        assert result == "No changes today."
        assert mock_client.messages.create.call_count == 1

    def test_tool_use_then_end_turn(self, patched_daily, mocker):
        mock_client = MagicMock()
        mock_run = mocker.patch("daily_agent.subprocess.run")
        mock_run.return_value = MagicMock(stdout="M note.md\n", stderr="", returncode=0)
        mock_client.messages.create.side_effect = [
            _make_tool_use_response("git_diff", "tu_1"),
            _make_end_turn_response("Used git_diff."),
        ]
        mocker.patch("daily_agent.anthropic.Anthropic", return_value=mock_client)
        result = daily_agent.run_agent()
        assert result == "Used git_diff."
        assert mock_client.messages.create.call_count == 2

    def test_unexpected_stop_reason_raises(self, patched_daily, mocker):
        resp = MagicMock()
        resp.stop_reason = "max_tokens"
        resp.content = []
        mock_client = MagicMock()
        mock_client.messages.create.return_value = resp
        mocker.patch("daily_agent.anthropic.Anthropic", return_value=mock_client)
        with pytest.raises(RuntimeError, match="Unexpected stop_reason"):
            daily_agent.run_agent()
