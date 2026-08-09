# Changelog

All notable changes to the Agent Toolkit plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Update discipline:** this file must be updated on every version bump. `/release` does it for you; see `## Releasing` and `## Release Files` in `CLAUDE.md` for what it rewrites.

## [0.3.0] — 2026-08-09 — "Switchboard"

Claude Code 2.1.224 gives every session an inbox socket and a record on disk, and between them they replace both halves of this plugin's machinery. A worker is now addressed and reports its findings over cross-session messaging, and its state is read from the peer registry instead of parsed off cmux's event bus. The gain that matters is the case the bus could never cover at all: a worker that is killed runs no hook and publishes nothing, so it used to look exactly like a worker still working — which is why the two commits that landed after v0.2.0 were both about the watcher failing to say anything.

### Added

- **Workers report their findings in a message, and it arrives before the watcher notices.** A stage's outcome now interrupts the supervisor with the result in it, instead of being dug out of a transcript. Ask for the findings, not an acknowledgement — a reply saying "done" wastes the round trip.
- **`peer.py` and `surface.py` ship as files.** Name to socket address, status, cwd and session id; surface ref to uuid and back, plus pane and tty. They were shell functions in the draft, which quietly failed: every Bash call is a fresh shell, so a function defined once and used ten sections later asks for its whole definition again every time.
- **A fifth watcher signal, `CLEAR`.** A worker that stops being blocked without a turn running has nothing to collect, and saying so is what keeps `DONE` meaning what it claims.

### Changed

- **The watcher polls the peer registry instead of the cmux event bus**, and drops from 230 lines to 133 — no bus frames, no request-id de-duplication, no timing-collapse window, no `PreToolUse` fallback. It also no longer depends on cmux at all.
- **`EXIT` became `GONE`**, and now covers every way a worker stops running rather than only the graceful one. The `EXIT` shipped after v0.2.0 could not fire for a killed worker, because the signal is uncatchable at the source; polling process liveness has no such blind spot.
- **The launch name is the only join key.** No `--session-id`, no `uuidgen` — one string is the tab title, the registry key and the message address.
- **The skill documents the two channels as non-interchangeable.** Messages cannot clear a block or run a slash command; keys can do both. Both were measured, not assumed.
- README carries a 3× demo GIF of spawning three agents.

### Fixed

- **`DONE` no longer fires for a worker that did nothing.** Leaving a block counted as a turn ending, so a terminal overlay opening over an idle worker produced a completion for a session whose transcript never gained a record. `DONE` is now strictly working-to-idle.
- **An unknown `status` no longer swallows a turn end.** The field is open-ended — a supervisor was caught reporting `shell` — and keeping each unseen value as a state of its own stopped the transition out of it from being recognised.
- **Liveness no longer answers "alive" for every dead worker.** The registry file outlives the process, and the pid fallback was `os.kill(-1, 0)`, which addresses every process the user may signal and succeeds.
- **The ledger row is written before the worker is launched**, as the prose beside it always said; the code did the opposite, leaving a window where a live worker had no ledger entry.
- **A worker suspended on a question is reported as `ASK`.** A question suspends the turn rather than ending it, so a watcher listening only for endings was deaf to the single most common reason a worker needs a human — and a deaf watcher looks exactly like a worker still working.

## [0.2.0] — 2026-08-07 — "Summons"

A demo prompt short enough to be worth showing kept opening no tabs at all. The skill was not failing to match — it was matching the same ground the built-in subagent tool already owns and losing the tie, because "run several agents in parallel and report when they finish" describes a subagent perfectly. The only way to win was to say "in cmux tabs" out loud, which is precisely the detail a showcase should not have to spell out.

### Added

- **The word "spawn" is now a sufficient trigger on its own.** No mention of cmux, tabs or panes is required: `Spawn 3 agents: echo "ping", argue the Earth is flat, write a haiku. Report each as it lands.` routes here. The description also states outright that this is preferred over the built-in subagent/Task tool, which is the part that actually breaks the tie — a subagent is invisible and cannot be watched, clicked into, or taken over, which is the whole point of spawning one. A Rules entry backs it up for the case where the skill is already loaded and the tasks look small enough to be worth downgrading.

  The claim is deliberately narrow. Only "spawn" is taken; "run", "start" and "in parallel" stay with subagents, because a request that genuinely wants an invisible agent reads as "research X" or "look into Y" rather than "spawn one".

### Changed

- **Documented the frontmatter trap this release nearly shipped.** A `": "` inside a skill `description` ends the plain YAML scalar and the block fails to parse — and the failure is silent at runtime: the skill loads with empty metadata, every field dropped, so it never triggers again while nothing in its text looks wrong. Worse, the marketplace-level `claude plugin validate .` does not read skill frontmatter and passes happily; only `claude plugin validate ./plugins/agent-toolkit` catches it. Both are now written down in `CLAUDE.md`.

## [0.1.4] — 2026-08-06 — "Sentry"

The skill was smoke-tested end to end against live cmux surfaces for the first time. Every mechanism held — one split with three tabs inside it, `DONE`, `ATTN` and `EXIT` all observed on the wire, a worker spawned after the watcher was armed picked up from the reloaded ledger. What did not hold was the order the walkthrough puts them in: read top to bottom, it starts the work before anything is watching it.

### Fixed

- **The walkthrough never armed the watcher.** "Spawn one agent" ran from the ledger row straight to "only now send the task", and the `Monitor` appeared two sections later, under "Wait for a stage to finish" — a heading you reach only once the task is already running. The instruction to arm first existed solely in the Rules at the very bottom, so the body and the rules disagreed about the one step whose entire value is its timing. Arming is now a numbered part of the walkthrough, between the ledger write and the send, where the reader is already standing.
- **Nothing said the watcher block is the `Monitor` tool.** It was presented as an ordinary shell pipeline, which makes `Bash(run_in_background: true)` look like the same thing done more cheaply. It is not: only a `Monitor`'s stdout lines become notifications, a backgrounded `Bash` notifies once *on exit*, and `--reconnect` guarantees this pipeline never exits. Every `DONE` then lands in an output file nobody reads while `pgrep` shows a perfectly healthy watcher — the same silent deafness the ledger-path and timestamp warnings already existed to prevent, arriving through the one door they left open.
- **The registration wait was unbounded.** `until claude agents --json | grep -q "$SID"; do sleep 1; done` has no exit for a launch line that never started `claude` — a typo, or a `cd` into a directory whose profile lacks it. It does not fail, it spins until the harness SIGKILLs the whole call with the ledger row already written and the task never sent, which afterwards is indistinguishable from a worker that simply did nothing. It now gives up after 60s and says so.

## [0.1.3] — 2026-08-06 — "Scoped"

Two guarantees from the last release turned out to be advice rather than mechanism. A warning you have just read does not stop you making the mistake, and a check that lists every process on the machine is not a check.

### Fixed

- **The `cd` is now guarded, not merely warned about.** Two agents in a row dropped `cd <repo>` from the launch line despite the bolded paragraph saying it is not optional — the second immediately after quoting that paragraph back. The mitigation was attentional ("read the launch line back before sending it") and there was nothing to compare it against. `REPO` is now a variable in the anchor block, guarded alongside `$CMUX_WORKSPACE_ID` and `$CMUX_SURFACE_ID` and refusing anything without a `.git`, so an unset repo fails loudly at the guard instead of silently landing workers in the caller's cwd — where it only misbehaves when the caller happens to be somewhere else, which is what makes it so hard to notice.
- **The orphan-watcher check implicated everyone else's runs.** It said "kill only your own — the ledger path tells them apart" and then printed `pgrep -fl watch-workers.py`, which lists every watcher on the machine wrapped in 400-character shell preambles. An agent following it reported two healthy watchers as orphans, one of them its own supervisor's live Monitor. The check is now scoped to `${CMUX_SURFACE_ID}`, turning it from a reading exercise into a yes/no, and a watcher is only reapable once `ps -o ppid=` shows the `claude` that armed it is actually gone.
- **Cleanup had no stated order.** Closing surfaces, deleting the ledger and stopping the watcher lived in three separate places, and the sequence mattered: stopping the watcher before deleting the ledger leaves a window in which a late event names a worker already reported. It is now four numbered steps in one place. The anchor block also `touch`es the ledger, so a first run stops printing "no such file" to stderr and teaching readers to ignore it.

## [0.1.2] — 2026-08-06 — "Reaper"

The cleanup section treated closing the tabs as the end of a run. It isn't — the watcher is a process, and nothing was stopping it.

### Fixed

- **The PR-scope check printed help text instead of a diffstat.** The merge gate added in v0.1.1 called `gh pr diff --stat`, and `gh pr diff` has no `--stat` — it accepts `--patch`, `--name-only`, `--color` and `--exclude`. Passing an unknown flag prints usage and exits 0, so the step looked like it ran while showing nothing, and the approval it exists to inform was back to being uninformed. It now uses `git diff --stat main...HEAD` (three dots, against the merge base) plus a `gh pr view` count. Caught by running the step on this release rather than by reading it.
- **A spawned agent's watcher outlived the agent.** A `Monitor` armed with `persistent: true` runs until `TaskStop` or the end of the session that armed it, and a *spawned* agent's session ending does not reap it. Observed live at the end of a six-agent run: one agent had finished, its surface was closed and its ledger deleted, and its watcher was still streaming the global event bus. The cleanup section closed surfaces and pruned ledger rows and never mentioned the process. It now ends by stopping the watcher and checking `pgrep -fl watch-workers.py` for orphans — matching only your own, since another live run's watcher is indistinguishable except by the ledger path in its command line. Deleting the ledger file (not just its rows) is what makes a survivor harmless: a watcher whose ledger is gone matches no session id and reports nothing.

## [0.1.1] — 2026-08-06 — "Backstop"

Both shipped skills claimed guarantees they did not deliver, and this release closes the gap in every case rather than softening the claim. `cmux-spawn-agent`'s mandatory watcher was silently deaf, its guard checked the wrong variable, and its pane reuse could split into a dead tab; `/release`'s staging gate was advertised as making a half-applied release impossible while being structurally blind to the failure a broken `regex` entry actually causes. Nothing here changes what either skill is for — every change makes an existing promise true.

### Fixed

- **The mandatory worker watcher was totally deaf.** `watch-workers.py` returned a constant `0.0` when `occurred_at` was missing or unparseable, so the collapse window compared `0.0` against `0.0` and swallowed every subsequent event for that key — three real turn-ends produced zero output. Timestamps now fall back to arrival time on the same epoch scale, so an event without a parseable `occurred_at` is still reported.
- **Waiting on a single worker no longer takes a different code path.** The one-worker shortcut grepped `agent.hook.Stop` straight out of the stream, so it could not see `PermissionRequest` or `SessionEnd` — a blocked worker and a dead one both read as still working — and it relied on `grep -m1` ending the pipeline, which does not happen until the stream's next write. One worker now gets the same Monitor and the same ledger as ten.
- **Pane reuse could resolve to a dead pane.** Only the ledger's last row was consulted, so closing the newest tab sent the next spawn into a pane that no longer existed and split a second time. Every row is now scanned newest-first for a live pane. The workspace guard also refused only on an empty `CMUX_WORKSPACE_ID` while placement passes `--surface "$CMUX_SURFACE_ID"` and the ledger is keyed by it — the guard now checks what is actually used.
- **`/release`'s staging gate only looked for leftovers.** A Release Files entry that step 3 never rewrote — precisely what a failing `regex` entry produces — passed a clean tree unnoticed. The gate now also asserts every path from the block is staged, and reads `git status --porcelain`'s second column so a re-edited `MM` file stops slipping past a `' M'` scan. It runs between `git add` and `git commit`, while the tree is still recoverable, instead of after the half-applied release had already landed.
- **A release could land merged and untagged.** An explicit `X.Y.Z` that was already tagged failed only at step 7, after `gh pr merge`, poisoning the next release's "since the last tag" window; the tag is now checked free before anything is written. Declining the merge had no documented exit, so a re-run double-bumped and wrote a second CHANGELOG entry — there are now three named exits. An aborted run leaving exactly the release files dirty is no longer refused as a dirty tree, which had made the documented recovery unreachable.
- **The README's `Latest:` line described the previous release.** Its pattern captured only the version digits, so a bump left the codename and summary stale; it now spans the whole line and substitutes all three named groups, anchored so trailing text is a non-match rather than a silent truncation at the first close-paren. Every Conventional Commit type also has a CHANGELOG bucket now, and the manifests re-validate after the rewrite instead of only before it.

- **The merge approval was being asked for a diff nobody had seen.** Step 4 shows the release rewrite — four files, ~20 lines — and step 6 then opened a PR carrying the entire `$LAST..HEAD` window, eight files and some three hundred lines, while its body described only the CHANGELOG entry. The prompt that actually ships a release was the one with the least context behind it. Step 6 now prints `gh pr diff --stat`, states the real commit and file count in the prompt, and says plainly that this is not the diff approved a moment ago. `gh pr create` also pins `--base main` instead of inheriting the repo default, and silence is now explicitly treated as `n` rather than being an unhandled state in a skill otherwise fastidious about them.

- **The watcher command silently filtered out every worker when copied as written.** Its last argument was `"$LEDGER"`, a variable assigned in some earlier shell — and `Monitor` runs the command in a shell of its own, where it expands to nothing. The watcher then `stat`s an empty path, reads zero ledger rows, and discards every event as unknown: a watcher that runs happily and reports nothing, indistinguishable from a run where nothing has finished. The block now spells the path out from `$TMPDIR` and `$CMUX_SURFACE_ID`, which *are* in that shell's environment. Found by an agent following the skill literally, which is the only way this surfaces.

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
