# obsidian-agent

A Python automation tool that reads an Obsidian vault's daily notes and uses the Claude API to synthesize a weekly review, then opens a pull request with the result via GitHub Actions.

## Architecture

### Weekly Review Agent (`agent.py`)
- **`agent.py`** — Single entry point. Runs an agentic loop with Claude (tool_use) to read vault files and write the weekly review. Max 30 iterations.
- **`.github/workflows/weekly-review.yml`** — Callable GitHub Actions workflow. Checks out vault repo, runs agent, commits changes, opens PR.
- Vault file access is whitelisted to specific PARA prefixes (e.g. `1-Projects/`, `Daily Notes/`) to prevent path traversal.

### Daily Review Agent (`daily_agent.py`)
- **`daily_agent.py`** — Lightweight end-of-day health check. Scans today's daily note and any files modified that day. Suggests action items and wikilinks, flags orphan notes and tag typos, and writes a short review to `Daily Reviews/YYYY-MM-DD.md`. Max 30 iterations.
- **`.github/workflows/daily-review.yml`** — Callable GitHub Actions workflow. Same pattern as the weekly workflow.
- Adds a `git_diff` tool (read-only, allowlisted subcommands) so Claude can inspect what changed in the vault since the last commit.
- Write access is restricted to `Daily Reviews/` only.

## Environment Variables

### Weekly Review (`agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `WEEK` | Yes | — | ISO week string, e.g. `2026-W11` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

### Daily Review (`daily_agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `DATE` | Yes | — | ISO date string, e.g. `2026-03-29` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

## Development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY and VAULT_DIR

# Weekly review — safe local test
DRY_RUN=1 WEEK=2026-W11 python agent.py

# Daily review — safe local test
DRY_RUN=1 DATE=2026-03-29 python daily_agent.py
```

Use `DRY_RUN=1` when testing locally — it logs all file operations without writing anything.

## Security — Public Repository

This repo is public. Before committing anything, verify:

- No secrets, API keys, or tokens are hardcoded
- No private hostnames, internal URLs, or personal infrastructure details
- No `.env` files (covered by `.gitignore`, but double-check)
- No log files containing run output (also covered by `.gitignore`)
