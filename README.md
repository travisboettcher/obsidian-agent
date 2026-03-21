# obsidian-agent

A Python automation tool that reads an Obsidian vault's daily notes and uses Claude to synthesize a weekly review, then opens a pull request with the result via GitHub Actions.

Every Monday morning the agent wakes up, reads the previous week's daily notes, and writes a structured review note to `3-Resources/Weekly Reviews/<WEEK>.md` in your vault — then opens a PR so you can review it before merging.

## How it works

1. The vault's `weekly-review.yml` workflow triggers on a Monday schedule (or manually).
2. It calls the reusable workflow in this repo, which checks out both repos side-by-side.
3. `agent.py` runs an agentic loop with Claude: the model reads daily notes via tool calls, then writes the review file and optionally updates `Home.md`.
4. Changes are committed on a new branch and a pull request is opened against `main` in the vault.

```
obsidian-agent repo          your vault repo
──────────────────           ───────────────────────────────
weekly-review.yml ◄──call── .github/workflows/weekly-review.yml
agent.py                     Daily Notes/2026-W11-*.md  →  3-Resources/Weekly Reviews/2026-W11.md
```

## Setting up your vault

### 1. Add the caller workflow

Create `.github/workflows/weekly-review.yml` in your vault repository:

```yaml
name: Weekly Review

on:
  schedule:
    - cron: "0 8 * * 1"  # Monday 8am UTC
  workflow_dispatch:

jobs:
  weekly-review:
    permissions:
      contents: write
      pull-requests: write
    uses: travisboettcher/obsidian-agent/.github/workflows/weekly-review.yml@main
    secrets: inherit
```

### 2. Add your Anthropic API key

In your vault repository: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your key from [console.anthropic.com](https://console.anthropic.com) |

The workflow's `GITHUB_TOKEN` is provided automatically by Actions — no extra setup needed.

### 3. Vault structure

The agent expects daily notes named `YYYY-MM-DD.md` and a PARA-style directory layout:

```
vault/
├── Daily Notes/          ← agent reads these
├── 1-Projects/
├── 2-Areas/
├── 3-Resources/
│   └── Weekly Reviews/   ← agent writes here
├── 4-Archive/
└── Home.md               ← agent optionally updates this
```

Writes are restricted to `Daily Notes/`, `3-Resources/Weekly Reviews/`, and `Home.md`. All other paths are rejected.

## Running locally

```bash
git clone https://github.com/travisboettcher/obsidian-agent
cd obsidian-agent
pip install -r requirements.txt
cp .env.example .env          # add your ANTHROPIC_API_KEY and VAULT_DIR
DRY_RUN=1 WEEK=2026-W11 python agent.py
```

`DRY_RUN=1` logs all file operations without writing anything — safe for testing.

## Weekly review format

The agent writes a structured note with this shape:

```markdown
---
date: 2026-03-15
week: 2026-W11
tags:
  - weekly-review
  - resource
period: 2026-03-09 to 2026-03-15
---

# Weekly Review — Week 11, 2026

**Period:** March 9–15, 2026

## Key Themes This Week
## Active Work
### Projects Advanced
### Tasks Identified
## Insights & Reflections
## Notes for Next Week
## Related Daily Notes
## Updated Notes
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out vault |
| `WEEK` | Yes | — | ISO week string, e.g. `2026-W11` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

## Security

This repository is public. It contains no secrets, private hostnames, or personal data. The `.gitignore` excludes `.env` files and log output. Before contributing, verify your changes meet the same standard.
