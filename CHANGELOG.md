# Changelog

All notable changes to the Agent Toolkit plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Update discipline:** this file must be updated on every version bump. `/release` does it for you; see `## Releasing` and `## Release Files` in `CLAUDE.md` for what it rewrites.

## [0.1.0] — 2026-08-06 — "Surface"

Initial release. The `spawn-agent` skill moves out of a dotfiles-and-symlinks arrangement into a versioned plugin, and gains a host prefix so the marketplace stays open to non-cmux agent skills.

### Added

- **`/agent-toolkit:cmux-spawn-agent`** — spawn Claude Code agents into cmux surfaces and drive multi-stage pipelines across them. Assigns each worker's session id before launch, which is the join key to its registry status, its notifications, and its transcript. Places workers as tabs in a single agents pane (one split per run, at most), anchored on the caller's own workspace and surface rather than on whatever is focused. Waits by push — a `cmux events --category agent` stream filtered through the bundled `watch-workers.py` against the run's ledger — with a documented polling fallback for answers needed inside one turn. Keeps a per-caller TSV ledger so an interrupted run can still answer what it owes, and offers (never performs) cleanup of only the surfaces it opened.
- **`watch-workers.py`** — stdin filter over the cmux event bus that emits one line per worker turn end (`DONE` / `ATTN` / `EXIT`). Filters strictly by the ledger's session ids, because the bus is global across workspaces and config profiles — an unfiltered watcher fires on the user's own tabs and on the orchestrator's own turns. Collapses the four frames cmux delivers per turn end into one, and re-reads the ledger on change so workers spawned after the watcher was armed are picked up without restarting it.

- **`/release`** — this repo's own release ceremony, as a repo-local skill. Derives the bump from the Conventional Commits since the last tag, rewrites every file declared in `CLAUDE.md`'s `## Release Files` block (rather than a list hard-coded in the skill), shows the diff, commits, opens a PR, and asks a *second* time before merging. Ends by refreshing both local profiles — necessary because the plugin payload cache is keyed by version string, so an unbumped edit never reaches an installed profile.

### Changed

- **Renamed `spawn-agent` → `cmux-spawn-agent`.** The skill is bound to cmux's surface/pane model and its event bus; the prefix says so, and leaves the bare name free for a host-agnostic successor.
- **Bundled-script path is now `${CLAUDE_PLUGIN_ROOT}`-anchored.** The watcher was previously invoked through `<skill-dir>`, the base directory printed at skill load. The plugin-root token resolves wherever the plugin was installed from, so the pipeline no longer depends on that line being read correctly.
- **Profile guidance generalized.** The "two profiles" section described one specific dual-profile setup; it now describes the general case — `claude agents --json` reads only the active `CLAUDE_CONFIG_DIR`, so a supervisor watching one registry reports workers in another as gone.
