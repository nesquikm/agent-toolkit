# Agent Toolkit

A Claude Code plugin marketplace for **agent orchestration** — spawning, watching, and chaining Claude Code agents across visible terminal surfaces.

## What This Is

This repo is a **Claude Code plugin marketplace** containing one plugin. The plugin ships skills that let one Claude Code session drive other, *visible* Claude Code sessions.

The marketplace is deliberately named for agents, not for any one terminal. `cmux`-specific skills carry a `cmux-` prefix in their own name; skills for other hosts can land beside them without renaming anything.

## Structure

```
.claude-plugin/marketplace.json          → Marketplace catalog
plugins/agent-toolkit/                   → The plugin
├── .claude-plugin/plugin.json           → Plugin manifest
└── skills/
    └── cmux-spawn-agent/                → /agent-toolkit:cmux-spawn-agent
        ├── SKILL.md
        └── watch-workers.py             → stdin filter: cmux event bus → one line per worker turn end
.claude/skills/release/                  → /release — this repo's own release ceremony
```

## Conventions

- **Skill naming** — a skill bound to a specific terminal host is prefixed with that host (`cmux-`). Host-agnostic skills take a bare name.
- **Bundled scripts** — a skill references its own files through `${CLAUDE_PLUGIN_ROOT}/skills/<skill>/<file>`, never through a relative path or the base directory printed at load. That token is what makes the path correct regardless of where the plugin was installed from.
- **One plugin, many skills** — new agent skills go into `plugins/agent-toolkit/skills/`. A second plugin is warranted only when a bundle needs its own version line.
- **JSON formatting** — 2-space indent, trailing newline. `/release` rewrites these files in place; anything else makes a release diff rewrite whole files.

## Releasing

Run `/release` from this repo. It bumps the version, writes the CHANGELOG entry, commits, opens a PR, and asks before merging. It never bumps and merges without showing you the diff first.

The `## Release Files` block below is the single source of truth for what a release rewrites — `/release` reads it rather than hard-coding paths. Add a file here and the next release picks it up.

**The version bump is not cosmetic.** The plugin payload cache is keyed by version string: `claude plugin update` is a no-op unless the version actually changed, and the stale cached copy keeps being served. Editing a skill without bumping means the change never reaches an installed profile. (The marketplace *catalog* is read live off the working tree, so `marketplace.json` edits appear immediately — a confusing asymmetry when debugging.)

## Release Files

```yaml
files:
  - path: plugins/agent-toolkit/.claude-plugin/plugin.json
    kind: json
    field: version
  - path: .claude-plugin/marketplace.json
    kind: json
    field: plugins[0].version
  - path: CHANGELOG.md
    kind: changelog
  - path: README.md
    kind: regex
    pattern: 'Latest: \*\*v(?<version>\d+\.\d+\.\d+) — '
    replace: 'Latest: **v{version} — '
```

The plugin version and the marketplace entry's version must always agree — `claude plugin validate .` fails when they drift.

## Commit Convention

This repo follows [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/), enforced by `.git/hooks/commit-msg`.

**Subject** — `<type>(<scope>): <description>`, ≤ 72 characters.

- **Type** — one of `feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert`.
- **Scope** — the primary touched area (e.g., `skills/cmux-spawn-agent`, `marketplace`, `docs`).
- **Breaking change** — append `!`, or use a `BREAKING CHANGE:` body footer.

These types drive the bump `/release` proposes: `feat` → minor, `fix`/`perf`/`refactor` → patch, `!` or `BREAKING CHANGE:` → major.

**Release commits** carry a footer:

```
Release: vX.Y.Z "Codename"
```
