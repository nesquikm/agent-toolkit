# Agent Toolkit

A Claude Code plugin marketplace for **agent orchestration** — running work in separate, *visible* Claude Code sessions instead of inside your own context.

Subagents are cheap and invisible. Sometimes you want the opposite: a real session in a real tab, that the user can watch, interrupt, and take over by clicking on it. That is what this toolkit builds.

![Three agents spawned into cmux tabs, each reporting back as it finishes](docs/demo.gif)

*One prompt spawns three agents into their own tabs; the orchestrator reports each one as it lands. Recorded at 3× speed.*

## Install

```
/plugin marketplace add nesquikm/agent-toolkit
```

```
/plugin install agent-toolkit@agent-toolkit
```

## Skills

### `/agent-toolkit:cmux-spawn-agent`

Spawn Claude Code agents into [cmux](https://github.com/manaflow-ai/cmux) surfaces (tabs) and drive multi-stage pipelines across them.

The move that makes a visible agent controllable is **assigning its session id before launch** — that id is the join key to its registry status, its notifications, and its transcript. Everything else follows from it.

What the skill encodes:

- **Placement that doesn't disturb the user.** One split per run at most; every agent after the first is a tab in that same pane. Placement is anchored on the caller's own workspace and surface, never on whatever happens to be focused — `new-pane` has no target flag and will cut an unrelated pane in half while you sit somewhere else.
- **Push-based waiting.** A polling loop only runs while you are inside a turn, so any stage that outlives the turn is a stage nobody is watching. The bundled `watch-workers.py` filters cmux's event bus down to this run's workers and emits one line per turn end (`DONE` / `ATTN` / `EXIT`), each of which re-invokes the orchestrator. A polling fallback is documented for answers needed inside one turn.
- **A ledger that survives you.** Every spawn writes a TSV row keyed by the caller's surface; rows flip to `reported` only once the user has actually been told that worker's outcome. `awk -F'\t' '$5!="reported"'` is the answer to "what am I still owed?" at the start of any turn, with nothing remembered.
- **Cleanup as a proposal.** Only surfaces in this run's ledger are ever offered for closing, and only after the user confirms. A tab you did not spawn is the user's, however idle it looks.

The skill is written as a set of verified behaviours rather than an API tour — each rule in it exists because the obvious alternative was tried and silently did the wrong thing.

**Requires:** [cmux](https://github.com/manaflow-ai/cmux) (the skill refuses to spawn outside a cmux terminal), `python3`, and `claude` on `PATH`.

## Conventions

- A skill bound to a specific terminal host carries that host as a prefix (`cmux-`). Host-agnostic skills take a bare name — the marketplace is about agents, not about any one terminal.
- Bundled scripts are referenced through `${CLAUDE_PLUGIN_ROOT}/skills/<skill>/<file>`, so paths are correct wherever the plugin was installed from.

## Releases

Releases are cut with the repo-local `/release` skill: it derives the bump from the Conventional Commits since the last tag, rewrites every file listed in the `## Release Files` block of [`CLAUDE.md`](./CLAUDE.md), shows the diff, commits, opens a PR, and asks before merging.

The bump is not cosmetic: Claude Code serves a `github`-sourced plugin from a cache keyed by version string, so an unbumped edit never reaches anyone who installed it that way. (A `directory`-sourced install — how you'd develop against a local clone — reads the working tree directly and is live without one.)

See [`CHANGELOG.md`](./CHANGELOG.md) for the full release history.

Latest: **v0.3.0 — "Switchboard"** (workers take their task and report their findings over cross-session messaging, and the watcher reads the peer registry instead of the event bus, so a worker that is killed is finally noticed)

<!-- The line above is rewritten by /release and is matched by an anchored regex.
     Keep it on its own line and keep the closing paren last — text after that
     paren makes the pattern fail to match, which aborts the release. -->

## License

MIT — see [`LICENSE`](./LICENSE).
