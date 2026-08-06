# Changelog

All notable changes to the Agent Toolkit plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Update discipline:** this file must be updated on every version bump. `/release` does it for you; see `## Releasing` and `## Release Files` in `CLAUDE.md` for what it rewrites.

## [0.1.1] — 2026-08-06 — "Backstop"

Both shipped skills claimed guarantees they did not deliver, and this release closes the gap in every case rather than softening the claim. `cmux-spawn-agent`'s mandatory watcher was silently deaf, its guard checked the wrong variable, and its pane reuse could split into a dead tab; `/release`'s staging gate was advertised as making a half-applied release impossible while being structurally blind to the failure a broken `regex` entry actually causes. Nothing here changes what either skill is for — every change makes an existing promise true.

### Fixed

- **The mandatory worker watcher was totally deaf.** `watch-workers.py` returned a constant `0.0` when `occurred_at` was missing or unparseable, so the collapse window compared `0.0` against `0.0` and swallowed every subsequent event for that key — three real turn-ends produced zero output. Timestamps now fall back to arrival time on the same epoch scale, so an event without a parseable `occurred_at` is still reported.
- **Waiting on a single worker no longer takes a different code path.** The one-worker shortcut grepped `agent.hook.Stop` straight out of the stream, so it could not see `PermissionRequest` or `SessionEnd` — a blocked worker and a dead one both read as still working — and it relied on `grep -m1` ending the pipeline, which does not happen until the stream's next write. One worker now gets the same Monitor and the same ledger as ten.
- **Pane reuse could resolve to a dead pane.** Only the ledger's last row was consulted, so closing the newest tab sent the next spawn into a pane that no longer existed and split a second time. Every row is now scanned newest-first for a live pane. The workspace guard also refused only on an empty `CMUX_WORKSPACE_ID` while placement passes `--surface "$CMUX_SURFACE_ID"` and the ledger is keyed by it — the guard now checks what is actually used.
- **`/release`'s staging gate only looked for leftovers.** A Release Files entry that step 3 never rewrote — precisely what a failing `regex` entry produces — passed a clean tree unnoticed. The gate now also asserts every path from the block is staged, and reads `git status --porcelain`'s second column so a re-edited `MM` file stops slipping past a `' M'` scan. It runs between `git add` and `git commit`, while the tree is still recoverable, instead of after the half-applied release had already landed.
- **A release could land merged and untagged.** An explicit `X.Y.Z` that was already tagged failed only at step 7, after `gh pr merge`, poisoning the next release's "since the last tag" window; the tag is now checked free before anything is written. Declining the merge had no documented exit, so a re-run double-bumped and wrote a second CHANGELOG entry — there are now three named exits. An aborted run leaving exactly the release files dirty is no longer refused as a dirty tree, which had made the documented recovery unreachable.
- **The README's `Latest:` line described the previous release.** Its pattern captured only the version digits, so a bump left the codename and summary stale; it now spans the whole line and substitutes all three named groups, anchored so trailing text is a non-match rather than a silent truncation at the first close-paren. Every Conventional Commit type also has a CHANGELOG bucket now, and the manifests re-validate after the rewrite instead of only before it.

### Changed

- **`git checkout` is documented as this repo's deployment command.** A `directory`-source marketplace reads the working tree at skill-load time, so whichever branch is checked out here runs in every session in both profiles — user scope means unrelated projects too. Verified by probe sessions loading text byte-identical to a test branch's tip while `claude plugin list` reported a clean `0.1.0` pinned four commits behind `HEAD`. The version string is not evidence of what is loaded.
- **`cmux-spawn-agent`'s Rules say where the ledger lives.** The section told you to mark a row reported without naming the file — the one thing a context-less turn needs. `CLAUDE.md` also stopped duplicating the bump table it had already drifted from, and no longer promises that any new Release Files entry is picked up when only three `kind`s are implemented.

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
