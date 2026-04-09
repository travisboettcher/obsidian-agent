# obsidian-agent

A Python automation tool that reads an Obsidian vault's daily notes and uses the Claude API to run three complementary review workflows, then opens pull requests with the results via GitHub Actions.

The workflows are designed to feed into each other in sequence:

```
daily review  →  weekly review  →  incremental processing
(end of day)     (Monday AM)        (monthly)
```

## Architecture

### Daily Review Agent (`daily_agent.py`)
- **`daily_agent.py`** — Lightweight end-of-day health check. Scans today's daily note and any files modified that day. Suggests action items and wikilinks, flags orphan notes and tag typos, and writes a short review to `Daily Reviews/YYYY-MM-DD.md`. Max 30 iterations.
- **`.github/workflows/daily-review.yml`** — Callable GitHub Actions workflow. Checks out both repos, computes today's date, runs agent, commits changes, opens PR.
- Adds a `git_diff` tool (read-only, allowlisted subcommands: `log`, `diff`, `status`, `show`, `ls-files`) so Claude can inspect what changed in the vault since the last commit.
- Write access is restricted to `Daily Reviews/` only.

### Weekly Review Agent (`agent.py`)
- **`agent.py`** — Single entry point. Runs an agentic loop with Claude (tool_use) to read vault files and write the weekly review. Max 30 iterations. Consumes the daily notes (and daily reviews) produced during the week.
- **`.github/workflows/weekly-review.yml`** — Callable GitHub Actions workflow. Checks out vault repo, runs agent, commits changes, opens PR.
- Vault file access is whitelisted to specific PARA prefixes (e.g. `1-Projects/`, `Daily Notes/`) to prevent path traversal.
- Write access: `Daily Notes/`, `3-Resources/Weekly Reviews/`, `Home.md`.

### Incremental Processing Agent (`incremental_agent.py`)
- **`incremental_agent.py`** — Two-phase enrichment pass that runs monthly. Detects changed notes via git history and processes only the delta. Max 40 iterations.
- **`.github/workflows/incremental-processing.yml`** — Callable GitHub Actions workflow. Requires full git history (`fetch-depth: 0`). Accepts a `batch_mode` boolean input.
- **Phase 1 (Sonnet)** — Mechanical enrichment per note: frontmatter tags, wikilinks, summaries. Optional batch API mode (`BATCH_MODE=1`) for 50% cost savings; polls every 30 seconds, times out after 2 hours.
- **Phase 2 (Opus)** — Agentic synthesis loop: MOC maintenance, cross-note linking, promotion/demotion suggestions, gap identification. Uses prompt caching for `Home.md` and the changed-files context window.
- State is persisted in `.obsidian-agent-state.json` inside the vault (`last_processed_commit`, `last_run_timestamp`) so subsequent runs are incremental.
- Write access: all PARA prefixes (`Daily Notes/`, `1-Projects/`, `2-Areas/`, `3-Resources/`, `4-Archive/`, `Home.md`).

## Environment Variables

### Daily Review (`daily_agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `DATE` | Yes | — | ISO date string, e.g. `2026-03-29` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

### Weekly Review (`agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `WEEK` | Yes | — | ISO week string, e.g. `2026-W11` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

### Incremental Processing (`incremental_agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `BATCH_MODE` | No | `0` | Set to `1` to use Batch API for Phase 1 (50% cost savings, slower) |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

## Development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY and VAULT_DIR

# Daily review — safe local test
DRY_RUN=1 DATE=2026-03-29 python daily_agent.py

# Weekly review — safe local test
DRY_RUN=1 WEEK=2026-W11 python agent.py

# Incremental processing — safe local test
DRY_RUN=1 python incremental_agent.py

# Incremental with batch API (Phase 1 only, 50% cheaper)
DRY_RUN=1 BATCH_MODE=1 python incremental_agent.py
```

Use `DRY_RUN=1` when testing locally — it logs all file operations without writing anything.

## Security — Public Repository

This repo is public. Before committing anything, verify:

- No secrets, API keys, or tokens are hardcoded
- No private hostnames, internal URLs, or personal infrastructure details
- No `.env` files (covered by `.gitignore`, but double-check)
- No log files containing run output (also covered by `.gitignore`)
