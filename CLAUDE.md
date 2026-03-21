# obsidian-agent

A Python automation tool that reads an Obsidian vault's daily notes and uses the Claude API to synthesize a weekly review, then opens a pull request with the result via GitHub Actions.

## Architecture

- **`agent.py`** — Single entry point. Runs an agentic loop with Claude (tool_use) to read vault files and write the weekly review. Max 30 iterations.
- **`.github/workflows/weekly-review.yml`** — Callable GitHub Actions workflow. Checks out vault repo, runs agent, commits changes, opens PR.
- Vault file access is whitelisted to specific PARA prefixes (e.g. `1-Projects/`, `Daily Notes/`) to prevent path traversal.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key (GitHub Actions secret) |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out Obsidian vault |
| `WEEK` | Yes | — | ISO week string, e.g. `2026-W11` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

## Development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY and VAULT_DIR
DRY_RUN=1 WEEK=2026-W11 python agent.py  # safe local test
```

Use `DRY_RUN=1` when testing locally — it logs all file operations without writing anything.

## Security — Public Repository

This repo is public. Before committing anything, verify:

- No secrets, API keys, or tokens are hardcoded
- No private hostnames, internal URLs, or personal infrastructure details
- No `.env` files (covered by `.gitignore`, but double-check)
- No log files containing run output (also covered by `.gitignore`)
