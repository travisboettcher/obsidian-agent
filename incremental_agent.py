#!/usr/bin/env python3
"""
Incremental Vault Review Agent

Detects changed vault notes via git diff and performs:
  Phase 1 — Mechanical enrichment per note (Sonnet, optionally batched)
  Phase 2 — Deep reasoning / synthesis pass (Opus, agentic loop)

Environment variables:
  ANTHROPIC_API_KEY  — Claude API key
  VAULT_DIR          — path to the checked-out vault (default: ./vault)
  BATCH_MODE         — set to "1" to use Batch API for Phase 1 (50% cost savings)
  DRY_RUN            — set to "1" to skip all file writes (log-only mode)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"
MAX_TOKENS = 8192
MAX_ITERATIONS = 40

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "./vault")).resolve()
BATCH_MODE = os.environ.get("BATCH_MODE", "0") == "1"
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

STATE_FILE = VAULT_DIR / ".obsidian-agent-state.json"
BATCH_POLL_INTERVAL = 30   # seconds
BATCH_POLL_MAX = 240       # 2 hours max (240 × 30s)

# Paths under VAULT_DIR that write_file / append_to_file are allowed to touch
SAFE_WRITE_PREFIXES = [
    "Daily Notes/",
    "1-Projects/",
    "2-Areas/",
    "3-Resources/",
    "4-Archive/",
    "Home.md",
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _resolve_vault_path(rel_path: str) -> Path:
    """Resolve a relative path inside VAULT_DIR, rejecting traversal attacks."""
    target = (VAULT_DIR / rel_path).resolve()
    if not str(target).startswith(str(VAULT_DIR)):
        raise ValueError(f"Path escapes vault root: {rel_path!r}")
    return target


def list_files(subdir: str) -> str:
    """List files in a vault subdirectory."""
    try:
        target = _resolve_vault_path(subdir)
        if not target.exists():
            return f"Directory does not exist: {subdir}"
        names = sorted(p.name for p in target.iterdir() if p.is_file())
        if not names:
            return "(empty directory)"
        return "\n".join(names)
    except Exception as exc:
        return f"Error listing {subdir!r}: {exc}"


def read_file(path: str) -> str:
    """Read a file from the vault."""
    try:
        target = _resolve_vault_path(path)
        if not target.exists():
            return f"File not found: {path}"
        return target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading {path!r}: {exc}"


def _is_safe_write_path(path: str) -> bool:
    for prefix in SAFE_WRITE_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def write_file(path: str, content: str) -> str:
    """Write content to a file in the vault (safe paths only)."""
    if not _is_safe_write_path(path):
        return (
            f"Write rejected: {path!r} is not under an allowed prefix. "
            f"Allowed prefixes: {SAFE_WRITE_PREFIXES}"
        )
    if DRY_RUN:
        preview = content[:200] + ("..." if len(content) > 200 else "")
        return f"[DRY_RUN] Would write {len(content)} chars to {path!r}: {preview}"
    try:
        target = _resolve_vault_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path!r}"
    except Exception as exc:
        return f"Error writing {path!r}: {exc}"


def append_to_file(path: str, content: str) -> str:
    """Append content to a file in the vault (safe paths only)."""
    if not _is_safe_write_path(path):
        return (
            f"Append rejected: {path!r} is not under an allowed prefix. "
            f"Allowed prefixes: {SAFE_WRITE_PREFIXES}"
        )
    if DRY_RUN:
        preview = content[:200] + ("..." if len(content) > 200 else "")
        return f"[DRY_RUN] Would append {len(content)} chars to {path!r}: {preview}"
    try:
        target = _resolve_vault_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} chars to {path!r}"
    except Exception as exc:
        return f"Error appending to {path!r}: {exc}"


def search_notes_by_tag(tag: str) -> str:
    """Search the vault for notes that have a specific tag in their frontmatter."""
    matches = []
    try:
        for md_file in VAULT_DIR.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
                if not fm_match:
                    continue
                frontmatter = fm_match.group(1)
                in_tags = False
                for line in frontmatter.splitlines():
                    stripped = line.strip()
                    if re.match(r"^tags\s*:", stripped):
                        in_tags = True
                        inline = re.search(r"\[([^\]]*)\]", stripped)
                        if inline:
                            items = [t.strip().strip("\"'") for t in inline.group(1).split(",")]
                            if tag in items:
                                matches.append(str(md_file.relative_to(VAULT_DIR)))
                            in_tags = False
                        continue
                    if in_tags:
                        if stripped.startswith("- "):
                            item = stripped[2:].strip().strip("\"'")
                            if item == tag:
                                matches.append(str(md_file.relative_to(VAULT_DIR)))
                        elif stripped and not stripped.startswith("#"):
                            in_tags = False
            except Exception:
                continue
    except Exception as exc:
        return f"Error searching vault: {exc}"

    if not matches:
        return f"No notes found with tag: {tag}"
    return "\n".join(sorted(matches))


def check_note_exists(note_name: str) -> str:
    """Check whether a note exists in the vault by name or path."""
    note_name = note_name.strip()
    candidate = note_name if note_name.endswith(".md") else note_name + ".md"
    # Try as a direct vault-relative path first
    try:
        direct = _resolve_vault_path(candidate)
        if direct.exists():
            return f"EXISTS: {str(direct.relative_to(VAULT_DIR))}"
    except ValueError:
        pass
    # Recursive search by filename only
    search_name = Path(candidate).name
    for md_file in VAULT_DIR.rglob("*.md"):
        if md_file.name == search_name:
            return f"EXISTS: {str(md_file.relative_to(VAULT_DIR))}"
    return f"NOT FOUND: {note_name}"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_files",
        "description": (
            "List files in a vault subdirectory. "
            "Pass a path relative to the vault root, e.g. 'Daily Notes/' or '1-Projects/'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subdir": {"type": "string", "description": "Subdirectory path relative to vault root"}
            },
            "required": ["subdir"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the full text content of a vault file. "
            "Pass a path relative to the vault root, e.g. 'Daily Notes/2026-03-09.md'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to vault root"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a vault file (creates or overwrites). "
            "Allowed under: Daily Notes/, 1-Projects/, 2-Areas/, 3-Resources/, 4-Archive/, Home.md."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to vault root"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_to_file",
        "description": (
            "Append text to an existing vault file. "
            "Allowed under: Daily Notes/, 1-Projects/, 2-Areas/, 3-Resources/, 4-Archive/, Home.md."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to vault root"},
                "content": {"type": "string", "description": "Text to append"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "search_notes_by_tag",
        "description": (
            "Search the vault for notes that have a specific tag in their frontmatter. "
            "Returns vault-relative paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to search for (without #)"}
            },
            "required": ["tag"],
        },
    },
    {
        "name": "check_note_exists",
        "description": (
            "Check whether a note exists in the vault by name or path. "
            "Useful for finding stub wikilinks that point to missing notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "note_name": {
                    "type": "string",
                    "description": "Note name (e.g. 'Project Alpha' or '1-Projects/Alpha.md')",
                }
            },
            "required": ["note_name"],
        },
    },
]


def dispatch_tool(name: str, inputs: dict) -> str:
    if name == "list_files":
        return list_files(inputs["subdir"])
    if name == "read_file":
        return read_file(inputs["path"])
    if name == "write_file":
        return write_file(inputs["path"], inputs["content"])
    if name == "append_to_file":
        return append_to_file(inputs["path"], inputs["content"])
    if name == "search_notes_by_tag":
        return search_notes_by_tag(inputs["tag"])
    if name == "check_note_exists":
        return check_note_exists(inputs["note_name"])
    return f"Unknown tool: {name!r}"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load agent state from STATE_FILE."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"last_processed_commit": None, "last_run_timestamp": None}


def save_state(commit_sha: str) -> None:
    """Persist agent state to STATE_FILE (skipped in DRY_RUN)."""
    import datetime
    state = {
        "last_processed_commit": commit_sha,
        "last_run_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    if DRY_RUN:
        print(f"[agent] DRY_RUN: Would save state: {json.dumps(state)}", file=sys.stderr)
        return
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[agent] State saved → {STATE_FILE}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Git diff integration
# ---------------------------------------------------------------------------

def get_current_head(vault_dir: Path) -> str:
    """Return the current HEAD commit SHA of the vault repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Vault directory is not a git repository or git failed: {vault_dir}\n{exc.stderr}"
        ) from exc


def get_changed_markdown_files(vault_dir: Path, since_commit: str) -> list[str]:
    """Return vault-relative paths of .md files changed since since_commit."""
    result = subprocess.run(
        ["git", "-C", str(vault_dir), "diff", "--name-only", since_commit, "HEAD"],
        check=True, capture_output=True, text=True,
    )
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [p for p in paths if p.endswith(".md")]


# ---------------------------------------------------------------------------
# System prompts (with prompt caching)
# ---------------------------------------------------------------------------

_STATIC_ENRICHMENT = """\
You are a mechanical note enrichment assistant for an Obsidian vault using the PARA method.
Analyse the provided note and return a JSON object with enrichment suggestions.

Return ONLY a JSON object (no markdown fences, no explanation) with these keys:
- summary (string, 1-2 sentences describing what the note is about)
- tags (array of strings, relevant tags without # prefix)
- status (string: "active" | "reference" | "stub" | "archived" | null if unclear)
- wikilinks (array of note names that likely exist in the vault and are relevant to this note)
- missing_fields (array of frontmatter field names that are absent but would be useful)
"""

_STATIC_OPUS = """\
You are a deep knowledge synthesis assistant for an Obsidian vault organised with the PARA method.
The vault directories are:

  1-Projects/   — active projects with a defined outcome
  2-Areas/      — ongoing responsibilities
  3-Resources/  — reference material
  4-Archive/    — completed or inactive items
  Daily Notes/  — daily capture notes (named YYYY-MM-DD.md)

You have access to these tools:
  list_files(subdir)            — list files in a vault directory
  read_file(path)               — read a vault file
  write_file(path, content)     — write/overwrite a vault file
  append_to_file(path, content) — append to a vault file
  search_notes_by_tag(tag)      — find notes with a specific frontmatter tag
  check_note_exists(note_name)  — check if a note exists (for validating wikilinks)

== YOUR TASKS ==

1. SYNTHESIS
   For each changed note: read it, follow wikilinks and search tags to find related notes.
   If a meaningful connection, contradiction, or emergent pattern exists, add or update a
   "## Synthesis" section in the note with a 2-5 sentence observation. Skip trivial links.

2. NOTE ENRICHMENT REVIEW
   Phase 1 (Sonnet) has already applied mechanical enrichment. Add any confirmed wikilinks
   you verify via check_note_exists that are not yet present.

3. MOC MAINTENANCE
   For each changed note, check if a Map of Content (MOC) file exists for its topic cluster
   (typically "*MOC.md" or "* Index.md"). If found, add a wikilink to the changed note if
   missing. If no MOC exists and 3+ related notes share a topic, create one.

4. PROMOTION / DEMOTION SUGGESTIONS
   Evaluate changed notes for reclassification (Resource→Project, Project→Archive, etc.).
   Do NOT move files. Record suggestions in your final REPORT.

5. GAP IDENTIFICATION
   Extract all [[Note Name]] patterns from changed notes. Use check_note_exists for each.
   List missing notes in your final REPORT.

== FINAL REPORT ==
End your final message with a REPORT: block:

REPORT:
## Synthesis Observations
[bullet list of synthesis findings written to notes]

## MOC Updates
[bullet list of MOC files created or updated]

## Promotion/Demotion Suggestions
[bullet list of reclassification suggestions with reasoning]

## Knowledge Gaps
[bullet list of stub wikilinks pointing to non-existent notes]

== CONSTRAINTS ==
- Do NOT reorganise, rename, or delete any files.
- Only write to allowed PARA prefixes and Home.md.
- Synthesis sections: 2-5 sentences maximum.
- If a vault file cannot be read, skip it and continue.
"""


def build_cached_system_prompt(moc_content: str, changed_files_context: str) -> list[dict]:
    """Build a prompt-cached system prompt list for Phase 1 (Sonnet enrichment)."""
    return [
        {"type": "text", "text": _STATIC_ENRICHMENT},
        {
            "type": "text",
            "text": f"## Vault MOC\n\n{moc_content}",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"## Changed Files This Run\n\n{changed_files_context}",
        },
    ]


def build_opus_system_prompt(moc_content: str, changed_files_summary: str) -> list[dict]:
    """Build a prompt-cached system prompt list for Phase 2 (Opus deep reasoning)."""
    return [
        {"type": "text", "text": _STATIC_OPUS},
        {
            "type": "text",
            "text": f"## Vault MOC\n\n{moc_content}",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"## Changed Files This Run\n\n{changed_files_summary}",
        },
    ]


# ---------------------------------------------------------------------------
# YAML frontmatter handling (stdlib only — no pyyaml)
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_SPECIAL_RE = re.compile(r'[:#\[\]{},&*?|<>=!%@`\'"\\n]')


def _yaml_quote(value: str) -> str:
    """Quote a YAML scalar if it contains special characters."""
    if _YAML_SPECIAL_RE.search(value) or value.startswith(" ") or value.endswith(" "):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from Markdown content.
    Returns (fields_dict, body_str). Unknown/complex lines stored in '_raw_lines'.
    """
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    fm_text = m.group(1)
    body = content[m.end():]
    fields: dict = {}
    raw_lines: list[str] = []
    current_list_key: str | None = None

    for line in fm_text.splitlines():
        # List item continuation
        if current_list_key is not None and re.match(r"^\s+-\s", line):
            item = re.sub(r"^\s+-\s+", "", line).strip().strip("\"'")
            fields[current_list_key].append(item)
            continue
        else:
            current_list_key = None

        kv = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val == "":
                fields[key] = []  # initialize as empty list for multi-line list values
                current_list_key = key
            elif val == "[]":
                fields[key] = []
            elif val.startswith("["):
                inner = val.strip("[]")
                items = [i.strip().strip("\"'") for i in inner.split(",") if i.strip()]
                fields[key] = items
            else:
                fields[key] = val.strip("\"'")
        else:
            raw_lines.append(line)

    if raw_lines:
        fields["_raw_lines"] = raw_lines
    return fields, body


def serialize_frontmatter(fields: dict) -> str:
    """Reconstruct a YAML frontmatter block from a fields dict."""
    lines = ["---"]
    raw_lines = fields.get("_raw_lines", [])
    for key, value in fields.items():
        if key == "_raw_lines":
            continue
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}:")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {_yaml_quote(str(item))}")
        elif value is None:
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {_yaml_quote(str(value))}")
    for raw in raw_lines:
        lines.append(raw)
    lines.append("---")
    return "\n".join(lines) + "\n"


def apply_enrichment(original_content: str, suggestions: dict) -> str:
    """
    Merge Sonnet enrichment suggestions into a note.
    Non-destructive: does not overwrite existing frontmatter fields (except summary).
    """
    fields, body = parse_frontmatter(original_content)

    # summary — always overwrite with AI-generated one
    if suggestions.get("summary"):
        fields["summary"] = suggestions["summary"]

    # tags — dedup-merge
    existing_tags = fields.get("tags") or []
    if not isinstance(existing_tags, list):
        existing_tags = [existing_tags]
    merged_tags = list(existing_tags)
    for t in suggestions.get("tags") or []:
        if t not in merged_tags:
            merged_tags.append(t)
    if merged_tags:
        fields["tags"] = merged_tags

    # status — set only if missing
    if not fields.get("status") and suggestions.get("status"):
        fields["status"] = suggestions["status"]

    new_fm = serialize_frontmatter(fields)
    if FRONTMATTER_RE.match(original_content):
        updated = new_fm + body
    else:
        updated = new_fm + "\n" + original_content

    # Append ## Related Notes section if not already present
    wikilinks = suggestions.get("wikilinks") or []
    if wikilinks and "## Related Notes" not in updated:
        wl_lines = "\n".join(f"- [[{wl}]]" for wl in wikilinks)
        updated += f"\n## Related Notes\n\n{wl_lines}\n"

    return updated


# ---------------------------------------------------------------------------
# Phase 1: Sonnet enrichment
# ---------------------------------------------------------------------------

def build_enrichment_params(file_path: str, file_content: str, system_blocks: list[dict]) -> dict:
    """Build API call params for a single Sonnet enrichment request."""
    return {
        "model": SONNET_MODEL,
        "max_tokens": 1024,
        "system": system_blocks,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"File: {file_path}\n\n{file_content}\n\n"
                    "Return ONLY a JSON object (no markdown fences) with keys:\n"
                    "- summary (string, 1-2 sentences)\n"
                    "- tags (array of strings)\n"
                    "- status (string: 'active'|'reference'|'stub'|'archived'|null if unclear)\n"
                    "- wikilinks (array of note names likely in the vault and relevant)\n"
                    "- missing_fields (array of frontmatter field names absent but useful)"
                ),
            }
        ],
    }


def _parse_enrichment_response(text: str) -> dict:
    """Parse JSON from a Sonnet enrichment response, with fallback regex extraction."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def run_enrichment_immediate(
    client: anthropic.Anthropic,
    tasks: list[tuple[str, str]],
    system_blocks: list[dict],
) -> dict[str, dict]:
    """Run Sonnet enrichment sequentially. Returns {file_path: suggestions}."""
    results: dict[str, dict] = {}
    for i, (file_path, file_content) in enumerate(tasks, 1):
        print(f"[agent] Enriching {i}/{len(tasks)}: {file_path}", file=sys.stderr)
        params = build_enrichment_params(file_path, file_content, system_blocks)
        response = client.messages.create(**params)
        usage = response.usage
        if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
            print(f"[agent] cache_read_input_tokens={usage.cache_read_input_tokens}", file=sys.stderr)
        text = next((b.text for b in response.content if b.type == "text"), "")
        results[file_path] = _parse_enrichment_response(text)
        print(f"[agent]   suggestions keys={list(results[file_path].keys())}", file=sys.stderr)
    return results


def submit_enrichment_batch(
    client: anthropic.Anthropic,
    tasks: list[tuple[str, str]],
    system_blocks: list[dict],
) -> str:
    """Submit a Batch API request for Phase 1 enrichment. Returns batch ID."""
    requests = [
        {"custom_id": file_path, "params": build_enrichment_params(file_path, content, system_blocks)}
        for file_path, content in tasks
    ]
    batch = client.messages.batches.create(requests=requests)
    print(f"[agent] Batch submitted: id={batch.id}", file=sys.stderr)
    return batch.id


def poll_batch(client: anthropic.Anthropic, batch_id: str):
    """Poll until batch completes. Raises TimeoutError after BATCH_POLL_MAX attempts."""
    for attempt in range(BATCH_POLL_MAX):
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"[agent] Batch poll #{attempt + 1}: processing={counts.processing} "
            f"succeeded={counts.succeeded} errored={counts.errored}",
            file=sys.stderr,
        )
        if batch.processing_status == "ended":
            return batch
        time.sleep(BATCH_POLL_INTERVAL)
    raise TimeoutError(
        f"Batch {batch_id} did not complete within {BATCH_POLL_MAX * BATCH_POLL_INTERVAL} seconds"
    )


def collect_batch_results(client: anthropic.Anthropic, batch_id: str) -> dict[str, dict]:
    """Collect and parse results from a completed batch."""
    results: dict[str, dict] = {}
    for result in client.messages.batches.results(batch_id):
        file_path = result.custom_id
        if result.result.type == "errored":
            print(f"[agent] Batch result errored for {file_path!r}: {result.result.error}", file=sys.stderr)
            results[file_path] = {}
            continue
        text = next((b.text for b in result.result.message.content if b.type == "text"), "")
        results[file_path] = _parse_enrichment_response(text)
    return results


# ---------------------------------------------------------------------------
# Phase 2: Opus agentic loop
# ---------------------------------------------------------------------------

def _extract_report(text: str) -> str:
    """Extract the REPORT: section from the final Opus message."""
    idx = text.find("REPORT:")
    if idx == -1:
        return text.strip()
    return text[idx:].strip()


def run_opus_loop(
    client: anthropic.Anthropic,
    changed_paths: list[str],
    system_blocks: list[dict],
) -> str:
    """Run the Opus agentic loop over changed vault notes. Returns the REPORT body."""
    changed_list = "\n".join(f"- {p}" for p in changed_paths)
    messages = [
        {
            "role": "user",
            "content": (
                f"The following vault notes have changed since the last review:\n\n"
                f"{changed_list}\n\n"
                "Please perform the incremental vault review as described in your instructions."
            ),
        }
    ]
    print("[agent] Starting Phase 2 Opus agentic loop", file=sys.stderr)

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"[agent] Opus API call #{iteration}", file=sys.stderr)
        response = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=TOOLS,
            messages=messages,
        )
        usage = response.usage
        if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
            print(f"[agent] cache_read_input_tokens={usage.cache_read_input_tokens}", file=sys.stderr)

        messages.append({"role": "assistant", "content": response.content})
        print(
            f"[agent] stop_reason={response.stop_reason}  blocks={[b.type for b in response.content]}",
            file=sys.stderr,
        )

        if response.stop_reason == "end_turn":
            final_text = next((b.text for b in response.content if b.type == "text"), "")
            return _extract_report(final_text)

        if response.stop_reason != "tool_use":
            raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason!r}")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[agent]   tool={block.name} input={json.dumps(block.input)[:120]}", file=sys.stderr)
            result = dispatch_tool(block.name, block.input)
            print(f"[agent]   result={result[:120]}{'...' if len(result) > 120 else ''}", file=sys.stderr)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Opus loop did not finish within {MAX_ITERATIONS} iterations")


# ---------------------------------------------------------------------------
# Vault helper
# ---------------------------------------------------------------------------

def load_moc_content(vault_dir: Path) -> str:
    """Read Home.md as the vault MOC. Returns empty string if missing."""
    home = vault_dir / "Home.md"
    if not home.exists():
        print("[agent] Warning: Home.md not found; MOC content will be empty", file=sys.stderr)
        return ""
    return home.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        sys.exit(1)
    if not VAULT_DIR.exists():
        print(f"Error: VAULT_DIR does not exist: {VAULT_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"[agent] VAULT_DIR={VAULT_DIR}", file=sys.stderr)
    print(f"[agent] DRY_RUN={DRY_RUN}  BATCH_MODE={BATCH_MODE}", file=sys.stderr)

    client = anthropic.Anthropic()
    state = load_state()
    print(f"[agent] Last processed commit: {state['last_processed_commit']}", file=sys.stderr)

    current_head = get_current_head(VAULT_DIR)
    print(f"[agent] Current HEAD: {current_head}", file=sys.stderr)

    # Resolve the base commit for diffing
    last_commit = state.get("last_processed_commit")
    if last_commit:
        since_commit = last_commit
    else:
        # First run: diff against HEAD~1, or empty tree if single commit
        try:
            since_commit = subprocess.run(
                ["git", "-C", str(VAULT_DIR), "rev-parse", "HEAD~1"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            since_commit = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree SHA

    print(f"[agent] Diffing {since_commit}..HEAD", file=sys.stderr)
    changed_paths = get_changed_markdown_files(VAULT_DIR, since_commit)

    if not changed_paths:
        print("[agent] No changed .md files — nothing to do", file=sys.stderr)
        save_state(current_head)
        return

    print(f"[agent] Changed files ({len(changed_paths)}):", file=sys.stderr)
    for p in changed_paths:
        print(f"  {p}", file=sys.stderr)

    moc_content = load_moc_content(VAULT_DIR)
    changed_summary = "\n".join(f"- {p}" for p in changed_paths)

    # -------------------------------------------------------------------------
    # Phase 1: Sonnet enrichment
    # -------------------------------------------------------------------------
    print("[agent] === Phase 1: Sonnet enrichment ===", file=sys.stderr)
    system_blocks = build_cached_system_prompt(moc_content, changed_summary)

    tasks: list[tuple[str, str]] = []
    for rel_path in changed_paths:
        full_path = VAULT_DIR / rel_path
        if not full_path.exists():
            print(f"[agent] Skipping deleted file: {rel_path}", file=sys.stderr)
            continue
        tasks.append((rel_path, full_path.read_text(encoding="utf-8")))

    if tasks:
        if BATCH_MODE:
            print(f"[agent] Submitting batch for {len(tasks)} files", file=sys.stderr)
            batch_id = submit_enrichment_batch(client, tasks, system_blocks)
            completed_batch = poll_batch(client, batch_id)
            enrichment_results = collect_batch_results(client, completed_batch.id)
        else:
            enrichment_results = run_enrichment_immediate(client, tasks, system_blocks)

        for rel_path, original_content in tasks:
            suggestions = enrichment_results.get(rel_path, {})
            if not suggestions:
                print(f"[agent] No enrichment suggestions for {rel_path}", file=sys.stderr)
                continue
            enriched = apply_enrichment(original_content, suggestions)
            if enriched != original_content:
                result = write_file(rel_path, enriched)
                print(f"[agent] Enriched {rel_path}: {result}", file=sys.stderr)
            else:
                print(f"[agent] No changes needed for {rel_path}", file=sys.stderr)
    else:
        print("[agent] No existing files to enrich", file=sys.stderr)

    # -------------------------------------------------------------------------
    # Phase 2: Opus deep reasoning
    # -------------------------------------------------------------------------
    print("[agent] === Phase 2: Opus deep reasoning ===", file=sys.stderr)
    opus_system_blocks = build_opus_system_prompt(moc_content, changed_summary)
    pr_body = run_opus_loop(client, changed_paths, opus_system_blocks)

    save_state(current_head)
    print(pr_body)  # stdout → captured by workflow as PR body


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        main()
    except TimeoutError as exc:
        print(f"Batch timeout: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Agent failed: {exc}", file=sys.stderr)
        sys.exit(1)
