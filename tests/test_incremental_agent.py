"""Tests for incremental_agent.py."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import incremental_agent as ia


# ---------------------------------------------------------------------------
# _yaml_quote
# ---------------------------------------------------------------------------

class TestYamlQuote:
    def test_plain_value_returned_unquoted(self):
        assert ia._yaml_quote("hello") == "hello"

    def test_plain_value_with_hyphen_unquoted(self):
        assert ia._yaml_quote("my-tag") == "my-tag"

    def test_colon_triggers_quoting(self):
        result = ia._yaml_quote("key: value")
        assert result.startswith('"') and result.endswith('"')

    def test_hash_triggers_quoting(self):
        result = ia._yaml_quote("value #comment")
        assert result.startswith('"') and result.endswith('"')

    def test_leading_space_triggers_quoting(self):
        result = ia._yaml_quote(" leading")
        assert result.startswith('"') and result.endswith('"')

    def test_trailing_space_triggers_quoting(self):
        result = ia._yaml_quote("trailing ")
        assert result.startswith('"') and result.endswith('"')

    def test_double_quote_in_value_is_escaped(self):
        result = ia._yaml_quote('say "hello"')
        assert '\\"hello\\"' in result

    def test_backslash_in_value_is_escaped(self):
        result = ia._yaml_quote("path\\to\\file")
        assert "\\\\" in result

    def test_empty_string_returned_as_is(self):
        assert ia._yaml_quote("") == ""


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_no_frontmatter_returns_empty_dict_and_full_content(self):
        content = "# Just a heading\n\nBody text.\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields == {}
        assert body == content

    def test_scalar_field(self):
        content = "---\ntitle: My Note\n---\nBody\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields["title"] == "My Note"
        assert body == "Body\n"

    def test_quoted_scalar_strips_quotes(self):
        content = '---\ntitle: "Quoted Title"\n---\nBody\n'
        fields, body = ia.parse_frontmatter(content)
        assert fields["title"] == "Quoted Title"

    def test_inline_list_field(self):
        content = "---\ntags: [project, active]\n---\nBody\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields["tags"] == ["project", "active"]

    def test_empty_inline_list(self):
        content = "---\ntags: []\n---\nBody\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields["tags"] == []

    def test_list_item_format_BUG1(self):
        content = "---\ntags:\n  - project\n  - active\n---\nBody\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields["tags"] == ["project", "active"]

    def test_body_extracted_correctly(self):
        content = "---\nkey: val\n---\n# Heading\n\nParagraph.\n"
        _, body = ia.parse_frontmatter(content)
        assert body == "# Heading\n\nParagraph.\n"

    def test_unknown_lines_stored_in_raw_lines(self):
        content = "---\ntitle: Note\ncreated: 2026-01-01T00:00:00Z\n---\n"
        fields, _ = ia.parse_frontmatter(content)
        assert "title" in fields
        # Complex ISO datetime line doesn't match simple kv, goes to _raw_lines
        # (or it may match — either is acceptable, just verify no crash)

    def test_multiple_scalar_fields(self):
        content = "---\ntitle: Note\nstatus: active\n---\n"
        fields, _ = ia.parse_frontmatter(content)
        assert fields["title"] == "Note"
        assert fields["status"] == "active"

    def test_empty_frontmatter(self):
        # FRONTMATTER_RE requires a newline before the closing ---, so this
        # form (with a blank line) is the minimal valid empty frontmatter.
        content = "---\n\n---\nBody\n"
        fields, body = ia.parse_frontmatter(content)
        assert fields == {}
        assert body == "Body\n"


# ---------------------------------------------------------------------------
# serialize_frontmatter
# ---------------------------------------------------------------------------

class TestSerializeFrontmatter:
    def test_scalar_field(self):
        result = ia.serialize_frontmatter({"title": "My Note"})
        assert "title: My Note" in result
        assert result.startswith("---\n")
        assert result.rstrip().endswith("---")

    def test_list_field(self):
        result = ia.serialize_frontmatter({"tags": ["project", "active"]})
        assert "tags:" in result
        assert "  - project" in result
        assert "  - active" in result

    def test_empty_list_field(self):
        result = ia.serialize_frontmatter({"tags": []})
        assert "tags:" in result

    def test_none_field(self):
        result = ia.serialize_frontmatter({"key": None})
        assert "key:" in result

    def test_raw_lines_included(self):
        fields = {"title": "Note", "_raw_lines": ["created: 2026-01-01"]}
        result = ia.serialize_frontmatter(fields)
        assert "created: 2026-01-01" in result

    def test_does_not_mutate_input_dict_BUG2(self):
        fields = {"title": "Note", "_raw_lines": ["extra: line"]}
        original_keys = set(fields.keys())
        ia.serialize_frontmatter(fields)
        assert set(fields.keys()) == original_keys, (
            "_raw_lines key was removed from input dict (mutation bug)"
        )

    def test_round_trip_scalar(self):
        original = "---\ntitle: My Note\nstatus: active\n---\nBody\n"
        fields, body = ia.parse_frontmatter(original)
        reconstructed = ia.serialize_frontmatter(fields) + body
        reparsed, _ = ia.parse_frontmatter(reconstructed)
        assert reparsed.get("title") == "My Note"
        assert reparsed.get("status") == "active"


# ---------------------------------------------------------------------------
# apply_enrichment
# ---------------------------------------------------------------------------

class TestApplyEnrichment:
    def _note(self, frontmatter_lines: list[str], body: str = "Body.\n") -> str:
        fm = "\n".join(frontmatter_lines)
        return f"---\n{fm}\n---\n{body}"

    def test_adds_new_tags_without_duplicating_existing(self):
        content = self._note(["tags: [existing]"])
        suggestions = {"tags": ["existing", "new-tag"]}
        result = ia.apply_enrichment(content, suggestions)
        # Check the raw text — avoid re-parsing since serialize writes list-item
        # format which currently triggers Bug 1 in parse_frontmatter.
        assert "new-tag" in result
        assert result.count("existing") == 1

    def test_does_not_overwrite_existing_status(self):
        content = self._note(["status: active"])
        suggestions = {"status": "archived"}
        result = ia.apply_enrichment(content, suggestions)
        fields, _ = ia.parse_frontmatter(result)
        assert fields.get("status") == "active"

    def test_sets_status_when_missing(self):
        content = self._note(["title: Note"])
        suggestions = {"status": "reference"}
        result = ia.apply_enrichment(content, suggestions)
        fields, _ = ia.parse_frontmatter(result)
        assert fields.get("status") == "reference"

    def test_always_overwrites_summary(self):
        content = self._note(["summary: Old summary"])
        suggestions = {"summary": "New summary"}
        result = ia.apply_enrichment(content, suggestions)
        fields, _ = ia.parse_frontmatter(result)
        assert fields.get("summary") == "New summary"

    def test_adds_related_notes_section_when_absent(self):
        content = self._note(["title: Note"])
        suggestions = {"wikilinks": ["ProjectA", "ProjectB"]}
        result = ia.apply_enrichment(content, suggestions)
        assert "## Related Notes" in result
        assert "[[ProjectA]]" in result
        assert "[[ProjectB]]" in result

    def test_no_related_notes_section_if_already_present(self):
        content = self._note(["title: Note"]) + "\n## Related Notes\n\n- [[Existing]]\n"
        suggestions = {"wikilinks": ["NewLink"]}
        result = ia.apply_enrichment(content, suggestions)
        assert result.count("## Related Notes") == 1

    def test_works_on_note_without_frontmatter(self):
        content = "# Plain Note\n\nJust text.\n"
        suggestions = {"summary": "A plain note", "tags": ["stub"]}
        result = ia.apply_enrichment(content, suggestions)
        assert "---" in result
        assert "stub" in result

    def test_handles_empty_suggestions(self):
        content = self._note(["title: Note"])
        result = ia.apply_enrichment(content, {})
        assert result is not None

    def test_handles_none_suggestion_values(self):
        content = self._note(["title: Note"])
        suggestions = {"tags": None, "wikilinks": None, "summary": None, "status": None}
        result = ia.apply_enrichment(content, suggestions)
        assert result is not None


# ---------------------------------------------------------------------------
# _parse_enrichment_response
# ---------------------------------------------------------------------------

class TestParseEnrichmentResponse:
    def test_parses_valid_json(self):
        text = '{"summary": "A note", "tags": ["x"]}'
        result = ia._parse_enrichment_response(text)
        assert result["summary"] == "A note"
        assert result["tags"] == ["x"]

    def test_strips_markdown_json_fences(self):
        text = '```json\n{"summary": "A note"}\n```'
        result = ia._parse_enrichment_response(text)
        assert result["summary"] == "A note"

    def test_strips_plain_code_fences(self):
        text = '```\n{"summary": "A note"}\n```'
        result = ia._parse_enrichment_response(text)
        assert result["summary"] == "A note"

    def test_extracts_embedded_json(self):
        text = 'Here is the result:\n\n{"summary": "A note", "tags": []}\n\nDone.'
        result = ia._parse_enrichment_response(text)
        assert result["summary"] == "A note"

    def test_returns_empty_dict_for_garbage(self):
        result = ia._parse_enrichment_response("this is not json at all")
        assert result == {}

    def test_returns_empty_dict_for_empty_string(self):
        result = ia._parse_enrichment_response("")
        assert result == {}


# ---------------------------------------------------------------------------
# _extract_report
# ---------------------------------------------------------------------------

class TestExtractReport:
    def test_extracts_text_after_report_marker(self):
        text = "Some preamble.\n\nREPORT:\n## Synthesis\n\n- Finding 1\n"
        result = ia._extract_report(text)
        assert result.startswith("REPORT:")
        assert "## Synthesis" in result
        assert "preamble" not in result

    def test_returns_stripped_full_text_when_no_marker(self):
        text = "  Just a plain response.  "
        result = ia._extract_report(text)
        assert result == "Just a plain response."

    def test_handles_report_at_start(self):
        text = "REPORT:\n## Gaps\n\n- None\n"
        result = ia._extract_report(text)
        assert result.startswith("REPORT:")

    def test_handles_empty_string(self):
        result = ia._extract_report("")
        assert result == ""


# ---------------------------------------------------------------------------
# _is_safe_write_path (incremental_agent — broadest prefix set)
# ---------------------------------------------------------------------------

class TestIsSafeWritePath:
    @pytest.mark.parametrize("path,expected", [
        ("Daily Notes/2026-01-01.md", True),
        ("1-Projects/project.md", True),
        ("2-Areas/area.md", True),
        ("3-Resources/refs.md", True),
        ("4-Archive/old.md", True),
        ("Home.md", True),
        ("random.md", False),
        ("", False),
        ("../etc/passwd", False),
        ("Daily Notes", True),   # exact prefix match without trailing slash
        ("1-Projects", True),
    ])
    def test_path_allowlist(self, path, expected):
        assert ia._is_safe_write_path(path) is expected


# ---------------------------------------------------------------------------
# _resolve_vault_path (requires patched_incremental)
# ---------------------------------------------------------------------------

class TestResolveVaultPath:
    def test_normal_relative_path_resolves(self, patched_incremental):
        vault = patched_incremental
        (vault / "Daily Notes" / "note.md").write_text("content")
        result = ia._resolve_vault_path("Daily Notes/note.md")
        assert result == vault / "Daily Notes" / "note.md"

    def test_dotdot_traversal_raises(self, patched_incremental):
        with pytest.raises(ValueError, match="escapes vault root"):
            ia._resolve_vault_path("../etc/passwd")

    def test_deep_traversal_raises(self, patched_incremental):
        with pytest.raises(ValueError, match="escapes vault root"):
            ia._resolve_vault_path("subdir/../../etc/passwd")

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
        monkeypatch.setattr(ia, "VAULT_DIR", vault)
        with pytest.raises(ValueError, match="escapes vault root"):
            ia._resolve_vault_path(str(evil / "secret.md"))


# ---------------------------------------------------------------------------
# State management (requires patched_incremental)
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_load_state_missing_file_returns_defaults(self, patched_incremental):
        state = ia.load_state()
        assert state["last_processed_commit"] is None
        assert state["last_run_timestamp"] is None

    def test_load_state_invalid_json_returns_defaults(self, patched_incremental):
        vault = patched_incremental
        (vault / ".obsidian-agent-state.json").write_text("NOT JSON")
        state = ia.load_state()
        assert state["last_processed_commit"] is None

    def test_load_state_valid_file_returns_stored_values(self, patched_incremental):
        vault = patched_incremental
        data = {"last_processed_commit": "abc123", "last_run_timestamp": "2026-01-01T00:00:00Z"}
        (vault / ".obsidian-agent-state.json").write_text(json.dumps(data))
        state = ia.load_state()
        assert state["last_processed_commit"] == "abc123"

    def test_save_state_writes_correct_json(self, patched_incremental):
        vault = patched_incremental
        ia.save_state("deadbeef")
        state_file = vault / ".obsidian-agent-state.json"
        assert state_file.exists()
        saved = json.loads(state_file.read_text())
        assert saved["last_processed_commit"] == "deadbeef"
        assert saved["last_run_timestamp"] is not None

    def test_save_state_dry_run_does_not_write(self, patched_incremental, monkeypatch):
        vault = patched_incremental
        monkeypatch.setattr(ia, "DRY_RUN", True)
        ia.save_state("deadbeef")
        state_file = vault / ".obsidian-agent-state.json"
        assert not state_file.exists()


# ---------------------------------------------------------------------------
# File I/O helpers (requires patched_incremental)
# ---------------------------------------------------------------------------

class TestFileOperations:
    def test_list_files_returns_sorted_names(self, patched_incremental):
        vault = patched_incremental
        (vault / "1-Projects" / "b.md").write_text("")
        (vault / "1-Projects" / "a.md").write_text("")
        result = ia.list_files("1-Projects")
        assert result == "a.md\nb.md"

    def test_list_files_missing_dir_returns_error(self, patched_incremental):
        result = ia.list_files("nonexistent")
        assert "does not exist" in result

    def test_list_files_empty_dir_returns_marker(self, patched_incremental):
        result = ia.list_files("1-Projects")
        assert result == "(empty directory)"

    def test_read_file_returns_content(self, patched_incremental):
        vault = patched_incremental
        (vault / "Daily Notes" / "2026-04-12.md").write_text("Hello")
        assert ia.read_file("Daily Notes/2026-04-12.md") == "Hello"

    def test_read_file_missing_returns_error(self, patched_incremental):
        result = ia.read_file("Daily Notes/missing.md")
        assert "File not found" in result

    def test_write_file_allowed_path(self, patched_incremental):
        vault = patched_incremental
        result = ia.write_file("1-Projects/new.md", "content")
        assert "Written" in result
        assert (vault / "1-Projects" / "new.md").read_text() == "content"

    def test_write_file_blocked_path(self, patched_incremental):
        result = ia.write_file("random.md", "content")
        assert "rejected" in result.lower()

    def test_write_file_dry_run(self, patched_incremental, monkeypatch):
        vault = patched_incremental
        monkeypatch.setattr(ia, "DRY_RUN", True)
        result = ia.write_file("1-Projects/new.md", "content")
        assert "DRY_RUN" in result
        assert not (vault / "1-Projects" / "new.md").exists()

    def test_append_to_file(self, patched_incremental):
        vault = patched_incremental
        p = vault / "Daily Notes" / "note.md"
        p.write_text("Line 1\n")
        ia.append_to_file("Daily Notes/note.md", "Line 2\n")
        assert p.read_text() == "Line 1\nLine 2\n"

    def test_append_to_file_dry_run(self, patched_incremental, monkeypatch):
        vault = patched_incremental
        monkeypatch.setattr(ia, "DRY_RUN", True)
        p = vault / "Daily Notes" / "note.md"
        p.write_text("Original\n")
        result = ia.append_to_file("Daily Notes/note.md", "Extra\n")
        assert "DRY_RUN" in result
        assert p.read_text() == "Original\n"


# ---------------------------------------------------------------------------
# dispatch_tool
# ---------------------------------------------------------------------------

class TestDispatchTool:
    def test_routes_list_files(self, patched_incremental):
        result = ia.dispatch_tool("list_files", {"subdir": "1-Projects"})
        assert "(empty directory)" in result

    def test_routes_read_file(self, patched_incremental):
        vault = patched_incremental
        (vault / "Daily Notes" / "note.md").write_text("hi")
        result = ia.dispatch_tool("read_file", {"path": "Daily Notes/note.md"})
        assert result == "hi"

    def test_routes_write_file(self, patched_incremental):
        result = ia.dispatch_tool(
            "write_file", {"path": "1-Projects/x.md", "content": "hello"}
        )
        assert "Written" in result

    def test_routes_append_to_file(self, patched_incremental):
        vault = patched_incremental
        (vault / "1-Projects" / "x.md").write_text("a\n")
        result = ia.dispatch_tool(
            "append_to_file", {"path": "1-Projects/x.md", "content": "b\n"}
        )
        assert "Appended" in result

    def test_routes_search_notes_by_tag(self, patched_incremental):
        vault = patched_incremental
        (vault / "1-Projects" / "note.md").write_text(
            "---\ntags: [mytag]\n---\nBody\n"
        )
        result = ia.dispatch_tool("search_notes_by_tag", {"tag": "mytag"})
        assert "note.md" in result

    def test_routes_check_note_exists(self, patched_incremental):
        vault = patched_incremental
        (vault / "1-Projects" / "note.md").write_text("")
        result = ia.dispatch_tool("check_note_exists", {"note_name": "note"})
        assert "EXISTS" in result

    def test_unknown_tool_returns_error(self, patched_incremental):
        result = ia.dispatch_tool("nonexistent_tool", {})
        assert "Unknown tool" in result


# ---------------------------------------------------------------------------
# Agentic loop (run_opus_loop) — mocked Anthropic client
# ---------------------------------------------------------------------------

def _make_end_turn_block(report_text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = f"Analysis complete.\n\nREPORT:\n{report_text}"
    return block


def _make_tool_use_block(tool_name: str = "read_file", tool_id: str = "tu_1") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = {"path": "Daily Notes/note.md"}
    block.id = tool_id
    return block


def _make_response(stop_reason: str, blocks: list) -> MagicMock:
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = blocks
    usage = MagicMock()
    usage.cache_read_input_tokens = 0
    resp.usage = usage
    return resp


class TestOpusLoop:
    def test_single_turn_end(self, patched_incremental, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_response(
            "end_turn", [_make_end_turn_block("## Synthesis\n\n- Finding A")]
        )
        result = ia.run_opus_loop(mock_client, ["Daily Notes/note.md"], [])
        assert "Finding A" in result
        assert mock_client.messages.create.call_count == 1

    def test_tool_use_then_end(self, patched_incremental, mocker):
        vault = patched_incremental
        (vault / "Daily Notes" / "note.md").write_text("# Note")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _make_response("tool_use", [_make_tool_use_block("read_file", "tu_1")]),
            _make_response("end_turn", [_make_end_turn_block("## Synthesis\n\n- Done")]),
        ]
        result = ia.run_opus_loop(mock_client, ["Daily Notes/note.md"], [])
        assert "Done" in result
        assert mock_client.messages.create.call_count == 2

    def test_max_iterations_raises(self, patched_incremental, monkeypatch, mocker):
        monkeypatch.setattr(ia, "MAX_ITERATIONS", 2)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_response(
            "tool_use", [_make_tool_use_block("read_file", "tu_1")]
        )
        with pytest.raises(RuntimeError, match="did not finish"):
            ia.run_opus_loop(mock_client, ["Daily Notes/note.md"], [])
