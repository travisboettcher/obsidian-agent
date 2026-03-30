#!/usr/bin/env python3
"""
Daily Review Vault Agent

Scans today's daily note and any files modified that day, then writes a
short end-of-day review to Daily Reviews/YYYY-MM-DD.md. Prints the PR
summary to stdout.

Environment variables:
  ANTHROPIC_API_KEY  — Claude API key
  VAULT_DIR          — path to the checked-out vault (default: ./vault)
  DATE               — ISO date string, e.g. 2026-03-29
  DRY_RUN            — set to "1" to skip all file writes (log-only mode)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-6"
MAX_TOKENS = 8192

VAULT_DIR = Path(os.environ.get("VAULT_DIR", "./vault")).resolve()
DATE = os.environ.get("DATE", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Paths under VAULT_DIR that write_file is allowed to touch
SAFE_WRITE_PREFIXES = [
    "Daily Reviews/",
]

# Git subcommands allowed by the git_diff tool (read-only operations only)
ALLOWED_GIT_SUBCOMMANDS = {"log", "diff", "status", "show", "ls-files"}


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


def git_diff(args: str) -> str:
    """Run a read-only git command in the vault directory."""
    parts = args.strip().split()
    if not parts:
        return "Error: no git arguments provided"
    subcommand = parts[0]
    if subcommand not in ALLOWED_GIT_SUBCOMMANDS:
        return (
            f"Git subcommand {subcommand!r} is not allowed. "
            f"Allowed: {sorted(ALLOWED_GIT_SUBCOMMANDS)}"
        )
    try:
        result = subprocess.run(
            ["git"] + parts,
            cwd=VAULT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return "(no output)"
        # Truncate to avoid flooding the context window
        if len(output) > 8000:
            return output[:8000] + "\n...(truncated)"
        return output
    except subprocess.TimeoutExpired:
        return "Error: git command timed out after 30 seconds"
    except Exception as exc:
        return f"Error running git {args!r}: {exc}"


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_files",
        "description": (
            "List files in a vault subdirectory. "
            "Pass a path relative to the vault root, e.g. 'Daily Notes/' or "
            "'1-Projects/'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subdir": {
                    "type": "string",
                    "description": "Subdirectory path relative to vault root",
                }
            },
            "required": ["subdir"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the full text content of a vault file. "
            "Pass a path relative to the vault root, e.g. 'Daily Notes/2026-03-29.md'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to vault root",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a vault file (creates or overwrites). "
            "Only allowed under: Daily Reviews/. "
            "Pass a path relative to the vault root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to vault root",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "git_diff",
        "description": (
            "Run a read-only git command in the vault directory to inspect recent "
            "changes. Allowed subcommands: log, diff, status, show, ls-files. "
            "Examples: 'status --short', "
            "'log --oneline --since=midnight --name-only', "
            "'diff HEAD~1 HEAD --stat'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": (
                        "Git arguments starting with an allowed subcommand, "
                        "e.g. 'status --short'"
                    ),
                }
            },
            "required": ["args"],
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
    if name == "git_diff":
        return git_diff(inputs["args"])
    return f"Unknown tool: {name!r}"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are a personal knowledge management assistant for an Obsidian vault
organised with the PARA method. The vault directories are:

  1-Projects/    — active projects with a defined outcome
  2-Areas/       — ongoing responsibilities
  3-Resources/   — reference material and weekly reviews
  4-Archive/     — completed or inactive items
  Daily Notes/   — daily capture notes (named YYYY-MM-DD.md)
  Daily Reviews/ — end-of-day review notes written by this agent (named YYYY-MM-DD.md)

Your task: produce a short daily review for {DATE}.

== WORKFLOW ==
1. Call git_diff("status --short") to see which vault files have uncommitted changes.
2. Call git_diff("log --oneline --since=midnight --name-only") to see today's commits
   and which files they touched.
3. Call read_file("Daily Notes/{DATE}.md") to read today's daily note.
   If the file does not exist, note that no daily capture was found for {DATE}.
4. Call read_file(...) on any other modified .md files identified in steps 1-2.
   Skip non-markdown files (images, attachments, etc.).
5. Analyse the content you have read:
   a. ACTION ITEMS — extract any tasks, TODOs, open loops, or commitments mentioned.
      Look for phrases like "need to", "follow up", "TODO", "- [ ]", "remind", etc.
   b. WIKILINKS — identify topics mentioned in plain text that likely correspond to
      an existing note. Use list_files() on relevant subdirectories (e.g.
      '1-Projects/', '2-Areas/', '3-Resources/') to check whether a matching file
      exists. Only suggest a link if a file with that name (or close match) exists.
      Do NOT invent links to files that don't exist.
   c. ORPHAN NOTES — check whether any .md files that appear as newly created today
      (added in git status or git log) are referenced by a [[wikilink]] anywhere in
      the files you have already read. If a new note is not linked from today's daily
      note or any other file you read, flag it as a possible orphan.
   d. TAG ISSUES — examine the YAML frontmatter tags in today's note and any other
      modified notes. Flag tags that look like typos (misspellings, inconsistent
      capitalisation, duplicate variants like "project" vs "projects").
6. Call read_file("Daily Reviews/{DATE}.md") to check whether a review already exists.
   If it does, update it in place rather than duplicating content.
7. Write the review to "Daily Reviews/{DATE}.md" using write_file.
   Use EXACTLY the following frontmatter and section structure — omit sections that
   have nothing to report, but keep the heading:

---
date: {DATE}
tags:
  - daily-review
---

# Daily Review — {DATE}

## Files Modified Today

## Action Items

## Suggested Wikilinks

## Issues Flagged

### Possible Orphan Notes

### Tag Observations

== CONSTRAINTS ==
- Do NOT reorganise, rename, or delete any vault files.
- Do NOT write to any path outside Daily Reviews/.
- Keep the review concise — bullet points are preferred over prose.
- If nothing was modified today and no daily note exists, write a minimal note
  stating that no activity was detected.

== SUMMARY ==
At the very end of your final message (after all tool calls are complete),
include a plain-text paragraph starting with "SUMMARY:" (on its own line).
Briefly describe what today's review found: how many files changed, how many
action items were identified, and any issues flagged.
"""


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_agent() -> str:
    """Run the agentic loop and return the PR body (the SUMMARY paragraph)."""
    client = anthropic.Anthropic()

    initial_prompt = (
        f"Please generate the daily review for {DATE}. "
        "Follow the workflow in your system prompt step by step."
    )

    messages = [{"role": "user", "content": initial_prompt}]

    print(f"[agent] Starting daily review for {DATE}", file=sys.stderr)
    if DRY_RUN:
        print("[agent] DRY_RUN=1: file writes will be logged only", file=sys.stderr)

    iteration = 0
    max_iterations = 30  # safety cap

    while iteration < max_iterations:
        iteration += 1
        print(f"[agent] API call #{iteration}", file=sys.stderr)

        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        print(
            f"[agent] stop_reason={response.stop_reason}  "
            f"blocks={[b.type for b in response.content]}",
            file=sys.stderr,
        )

        if response.stop_reason == "end_turn":
            # Extract final text for the SUMMARY
            final_text = ""
            for block in response.content:
                if block.type == "text":
                    final_text = block.text
            return _extract_summary(final_text)

        if response.stop_reason != "tool_use":
            raise RuntimeError(
                f"Unexpected stop_reason: {response.stop_reason!r}"
            )

        # Execute tool calls and collect results
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(
                f"[agent]   tool={block.name} input={json.dumps(block.input)[:120]}",
                file=sys.stderr,
            )
            result = dispatch_tool(block.name, block.input)
            print(
                f"[agent]   result={result[:120]}{'...' if len(result) > 120 else ''}",
                file=sys.stderr,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_iterations} iterations")


def _extract_summary(text: str) -> str:
    """Extract the SUMMARY: paragraph from the final assistant message."""
    marker = "SUMMARY:"
    idx = text.find(marker)
    if idx == -1:
        # Fall back to returning the whole final text as the PR body
        return text.strip()
    return text[idx + len(marker):].strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not DATE:
        print(
            "Error: DATE environment variable is not set (e.g. DATE=2026-03-29)",
            file=sys.stderr,
        )
        sys.exit(1)

    if not VAULT_DIR.exists():
        print(
            f"Error: VAULT_DIR does not exist: {VAULT_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        pr_body = run_agent()
        print(pr_body)  # stdout → captured by workflow as PR body
    except Exception as exc:
        print(f"Agent failed: {exc}", file=sys.stderr)
        sys.exit(1)
