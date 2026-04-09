# obsidian-agent

A Python automation tool that reads an Obsidian vault and uses Claude to run three complementary review workflows: a daily health check, a weekly synthesis, and an incremental enrichment pass. Each workflow feeds into the next.

```
daily review  →  weekly review  →  incremental processing
(end of day)     (Monday AM)        (after each merge)
```

## Workflows

### 1. Daily Review

Runs at end of day. Scans today's daily note and any vault files modified since the last commit. Flags action items, suggests wikilinks, identifies orphan notes and tag typos, and writes a short review to `Daily Reviews/YYYY-MM-DD.md`.

```
obsidian-agent repo            your vault repo
──────────────────             ────────────────────────────────────────
daily-review.yml ◄──call──    .github/workflows/daily-review.yml
daily_agent.py                 Daily Notes/2026-03-29.md  →  Daily Reviews/2026-03-29.md
```

### 2. Weekly Review

Runs every Monday morning. Reads the previous week's daily notes (and the daily reviews they produced) and synthesizes a structured retrospective, written to `3-Resources/Weekly Reviews/<WEEK>.md`.

```
obsidian-agent repo            your vault repo
──────────────────             ────────────────────────────────────────
weekly-review.yml ◄──call──   .github/workflows/weekly-review.yml
agent.py                       Daily Notes/2026-W11-*.md  →  3-Resources/Weekly Reviews/2026-W11.md
```

### 3. Incremental Processing

Runs after each workflow merge (or on demand). Uses git history to detect which notes changed since the last run, then performs two passes:

- **Phase 1 (Sonnet)** — mechanical enrichment per note: frontmatter tags, wikilinks, summaries. Optional batch API mode for 50% cost savings.
- **Phase 2 (Opus)** — agentic synthesis loop: MOC maintenance, cross-note linking, promotion/demotion suggestions, gap identification.

State is persisted in `.obsidian-agent-state.json` inside the vault so only changed notes are reprocessed on each run.

```
obsidian-agent repo                    your vault repo
──────────────────                     ──────────────────────────────────────
incremental-processing.yml ◄──call──  .github/workflows/incremental-processing.yml
incremental_agent.py                   changed notes  →  enriched notes + updated Home.md
```

## Setting up your vault

### 1. Add caller workflows

Create each of these files in your vault repository. You can add all three or only the ones you want.

**`.github/workflows/daily-review.yml`**
```yaml
name: Daily Review

on:
  schedule:
    - cron: "0 22 * * *"  # 10pm UTC daily
  workflow_dispatch:

jobs:
  daily-review:
    permissions:
      contents: write
      pull-requests: write
    uses: travisboettcher/obsidian-agent/.github/workflows/daily-review.yml@main
    secrets: inherit
```

**`.github/workflows/weekly-review.yml`**
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

**`.github/workflows/incremental-processing.yml`**
```yaml
name: Incremental Processing

on:
  pull_request:
    types: [closed]   # trigger after each review PR merges
  workflow_dispatch:
    inputs:
      batch_mode:
        description: "Use Batch API for Phase 1 (50% cost savings, slower)"
        type: boolean
        default: false

jobs:
  incremental-processing:
    if: github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true
    permissions:
      contents: write
      pull-requests: write
    uses: travisboettcher/obsidian-agent/.github/workflows/incremental-processing.yml@main
    with:
      batch_mode: ${{ inputs.batch_mode || false }}
    secrets: inherit
```

### 2. Add your Anthropic API key

In your vault repository: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your key from [console.anthropic.com](https://console.anthropic.com) |

The workflow's `GITHUB_TOKEN` is provided automatically by Actions — no extra setup needed.

### 3. Vault structure

The agents expect daily notes named `YYYY-MM-DD.md` and a PARA-style directory layout:

```
vault/
├── Daily Notes/          ← all agents read; daily agent writes here
├── Daily Reviews/        ← daily agent writes here
├── 1-Projects/
├── 2-Areas/
├── 3-Resources/
│   └── Weekly Reviews/   ← weekly agent writes here
├── 4-Archive/
└── Home.md               ← weekly + incremental agents update this
```

Write access per agent:

| Agent | Can write to |
|---|---|
| Daily | `Daily Reviews/` only |
| Weekly | `Daily Notes/`, `3-Resources/Weekly Reviews/`, `Home.md` |
| Incremental | All PARA prefixes + `Home.md` |

## Running locally

```bash
git clone https://github.com/travisboettcher/obsidian-agent
cd obsidian-agent
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY and VAULT_DIR

# Daily review — safe local test
DRY_RUN=1 DATE=2026-03-29 python daily_agent.py

# Weekly review — safe local test
DRY_RUN=1 WEEK=2026-W11 python agent.py

# Incremental processing — safe local test
DRY_RUN=1 python incremental_agent.py

# Incremental with batch API (Phase 1 only)
DRY_RUN=1 BATCH_MODE=1 python incremental_agent.py
```

`DRY_RUN=1` logs all file operations without writing anything — safe for testing.

## Weekly review format

The weekly agent writes a structured note with this shape:

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

### Daily Review (`daily_agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out vault |
| `DATE` | Yes | — | ISO date string, e.g. `2026-03-29` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

### Weekly Review (`agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out vault |
| `WEEK` | Yes | — | ISO week string, e.g. `2026-W11` |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

### Incremental Processing (`incremental_agent.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `VAULT_DIR` | Yes | `./vault` | Path to checked-out vault |
| `BATCH_MODE` | No | `0` | Set to `1` to use Batch API for Phase 1 (slower but 50% cheaper) |
| `DRY_RUN` | No | `0` | Set to `1` to skip all file writes |

## Security

This repository is public. It contains no secrets, private hostnames, or personal data. The `.gitignore` excludes `.env` files and log output. Before contributing, verify your changes meet the same standard.
