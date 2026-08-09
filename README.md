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

The move that makes a visible agent controllable is **naming it at launch** (`claude -n <name>`) — that name is the join key to its registry status, its notifications, and its transcript. Everything else follows from it.

What the skill encodes:

- **Placement that doesn't disturb the user.** One split per run at most; every agent after the first is a tab in that same pane. Placement is anchored on the caller's own workspace and surface, never on whatever happens to be focused — `new-pane` cannot be aimed at a pane and so will cut an unrelated pane in half while you sit somewhere else.
- **Push-based waiting.** A polling loop only runs while you are inside a turn, so any stage that outlives the turn is a stage nobody is watching. The bundled `watch-workers.py` polls Claude Code's peer registry, filtered down to this run's workers, and emits one line per state change (`DONE` / `ASK` / `ATTN` / `CLEAR` / `GONE`), each of which re-invokes the orchestrator. A sixth line, `WARN`, is not a worker signal at all — it names no worker and fires once, about 30 s in, to say the watcher matches no live session and is therefore watching nothing.
- **A ledger that survives you.** Every spawn writes a TSV row keyed by the caller's surface; rows flip to `reported` only once the user has actually been told that worker's outcome. `awk -F'\t' '$4!="reported"'` is the answer to "what am I still owed?" at the start of any turn, with nothing remembered.
- **Cleanup as a proposal.** Only surfaces in this run's ledger are ever offered for closing, and only after the user confirms. A tab you did not spawn is the user's, however idle it looks.

The skill is written as a set of verified behaviours rather than an API tour — each rule in it exists because the obvious alternative was tried and silently did the wrong thing.

**Requires:** [cmux](https://github.com/manaflow-ai/cmux) (the skill refuses to spawn outside a cmux terminal), `python3`, and `claude` on `PATH`.

### `/agent-toolkit:cmux-spawn-agent-smoke`

Makes the skill above prove itself on your machine — after an install, after an upgrade, or when a run has started feeling wrong. Ten checks, one split, one worker, about five minutes.

It is not a code review. It exercises the handful of things that have actually broken here and that reading cannot settle: that a worker lands in its own pane rather than covering the session you are talking to, that it becomes addressable under the name it was launched with, that the round trip is accepted on the worker's first attempt, and that teardown leaves no stranded tab and no orphaned watcher. Each check names the signal you look at and what its failure means, because several unrelated faults in this plugin share one signature and "it timed out" is not a diagnosis.

It opens with a staleness gate, which is the check most worth having and the one nobody thinks to write: skill text is resolved once at session start and cached for the life of that process, so a session that started before the plugin changed will smoke-test bytes nobody ships — and report a clean pass for them.

## Conventions

- A skill bound to a specific terminal host carries that host as a prefix (`cmux-`). Host-agnostic skills take a bare name — the marketplace is about agents, not about any one terminal.
- Bundled scripts are referenced through `${CLAUDE_PLUGIN_ROOT}/skills/<skill>/<file>`, so paths are correct wherever the plugin was installed from.

## Releases

Releases are cut with the repo-local `/release` skill: it derives the bump from the Conventional Commits since the last tag, rewrites every file listed in the `## Release Files` block of [`CLAUDE.md`](./CLAUDE.md), shows the diff, commits, opens a PR, and asks before merging.

The bump is not cosmetic: Claude Code serves a `github`-sourced plugin from a cache keyed by version string, so an unbumped edit never reaches anyone who installed it that way. (A `directory`-sourced install — how you'd develop against a local clone — reads the working tree directly and is live without one.)

See [`CHANGELOG.md`](./CHANGELOG.md) for the full release history.

Latest: **v0.4.0 — "Shakedown"** (an end-to-end audit against live workers — the watcher no longer swallows a DONE on a torn registry read, no longer goes deaf in silence, and the skill no longer tells you to kill the worker you are rescuing)

<!-- The line above is rewritten by /release and is matched by an anchored regex.
     Keep it on its own line and keep the closing paren last — text after that
     paren makes the pattern fail to match, which aborts the release. -->

## License

MIT — see [`LICENSE`](./LICENSE).
