# Agent Toolkit

A Claude Code plugin marketplace for **agent orchestration** — spawning, watching, and chaining Claude Code agents across visible terminal surfaces.

## What This Is

This repo is a **Claude Code plugin marketplace** containing one plugin. The plugin ships skills that let one Claude Code session drive other, *visible* Claude Code sessions.

The marketplace is deliberately named for agents, not for any one terminal. `cmux`-specific skills carry a `cmux-` prefix in their own name; skills for other hosts can land beside them without renaming anything.

## Structure

```
.claude-plugin/marketplace.json          → Marketplace catalog
plugins/agent-toolkit/                   → The plugin (the only thing that ships)
├── .claude-plugin/plugin.json           → Plugin manifest
└── skills/
    └── spawn-agent/                     → /agent-toolkit:spawn-agent
        ├── SKILL.md                     → the host-agnostic core; contains NO terminal commands
        ├── lib/                         → host-agnostic, used by every host
        │   ├── peer.py                  → worker name → socket address / status / cwd / sessionId
        │   └── watch-workers.py         → polls the peer registry → one line per worker state change
        └── hosts/                       → one file per terminal; you read exactly one
            ├── cmux.md                  → placement, launch, keys, close — cmux
            ├── cmux-surface.py          → cmux surface ref ↔ uuid, pane, tty
            └── herdr.md                 → the same, for herdr (needs no resolver script)
.claude/skills/                          → this repo's own skills; NOT shipped
├── release/                             → /release — the release ceremony
└── spawn-agent-smoke/                   → /spawn-agent-smoke — eleven checks against the working tree
```

## Conventions

- **One skill per capability, one file per host.** A skill that needs a terminal keeps a
  bare, host-free name and ships `hosts/<host>.md`; its `SKILL.md` names no terminal
  command at all, and the host file is the only place a command appears. This replaced an
  earlier convention of host-prefixed skill names (`cmux-spawn-agent`), which does not
  survive a second host: both descriptions match "spawn an agent", so the model has to
  guess the host at trigger time — before it has run anything that could tell it — and
  the ~60% of the text that is host-agnostic gets duplicated into two files that drift.
  A bare name keeps one trigger, and the host file is chosen *after* detection, from
  evidence.
- **Host detection is a precedence table, not a pair of checks** — `HERDR_ENV=1` beats a
  set `CMUX_SURFACE_ID`. Both can be set at once and the loser's variables stay valid:
  herdr panes inherit the environment of whatever started the herdr *server*, so
  `$CMUX_SURFACE_ID` inside herdr names the surface displaying the herdr window, is
  shared by every herdr pane on the machine, and makes every cmux command succeed at the
  wrong target. Measured 2026-08-12. Anything that adds a third host adds a row above,
  never a second `if`.
- **Bundled scripts** — a skill references its own files through `${CLAUDE_PLUGIN_ROOT}/skills/<skill>/<file>`, never through a relative path or the base directory printed at load. Host-agnostic scripts go in that skill's `lib/`, host-specific ones in its `hosts/`.
- **The smoke test does not ship.** It lives in `.claude/skills/spawn-agent-smoke/` and gets
  no `${CLAUDE_PLUGIN_ROOT}` — that token is substituted only inside a plugin. It resolves
  the plugin root itself from `git rev-parse --show-toplevel`, then *proves* the resolution
  by reading each profile's `plugins/known_marketplaces.json` and requiring a `directory`
  source whose `installLocation` is this tree. A `github` source there means the profile
  serves a version-keyed cache and the smoke test would be measuring bytes no session
  reads — which is a FAIL, not a footnote. The token is substituted **into the skill text at load time**, so the reading model sees an absolute path; it is *not* exported to the shell, and `echo "$CLAUDE_PLUGIN_ROOT"` from a Bash call prints nothing. Prose next to such a path must therefore still read correctly once the token has been replaced by a directory.
- **One plugin, many skills** — new agent skills go into `plugins/agent-toolkit/skills/`. A second plugin is warranted only when a bundle needs its own version line.
- **JSON formatting** — 2-space indent, trailing newline. `/release` rewrites these files in place; anything else makes a release diff rewrite whole files.
- **Skill frontmatter is a plain YAML scalar** — so a `description` may not contain `": "` (colon followed by a space). YAML reads it as a nested mapping and the whole block fails to parse, and the failure is *silent at runtime*: the skill loads with empty metadata, every field dropped, so it simply never triggers again. Nothing in the skill's own text looks wrong. Use an em dash where the sentence wants a colon, and run `claude plugin validate ./plugins/agent-toolkit` — the marketplace-level `claude plugin validate .` does **not** read skill frontmatter and passes happily while the skill is broken.
- **Never write a bare `$` followed by a digit in skill markdown.** When a skill is
  invoked *with arguments*, every `$0`, `$1`, … in its text is replaced by those
  arguments — **zero-indexed**, so `$0` is the first word — before the model reads a
  byte of it. Measured 2026-08-12 with a probe skill invoked as
  `/probe ZULU YANKEE XRAY WHISKEY VICTOR`\:

  | in the file | served as |
  | --- | --- |
  | `$0` `$1` `$2` `$4` | `ZULU` `YANKEE` `XRAY` `VICTOR` |
  | `awk -F'\t' '$4!="reported"'` | `awk -F'\t' 'VICTOR!="reported"'` |
  | `$$`, `${TMPDIR:-/tmp}`, `$LEDGER` | untouched |

  Only `$` + digit and `$ARGUMENTS` are rewritten; `$` + letter is safe. This is the
  same shape of failure as the frontmatter trap above — it produces *valid* awk that
  queries the wrong column, with no error anywhere — and it is worse in one way: it
  only fires when someone passes arguments, so a skill triggered by description match
  behaves perfectly right up until the day a user types the slash command with a
  sentence after it. Two of these were shipped in this repo before the probe found
  them. Write `awk -v c=4 '$c…'` and iterate with `$i`; if you genuinely need to show a
  literal `$1` in prose, say so in words rather than typing it.
  `grep -rn --include='*.md' -E '\$[0-9]' plugins .claude/skills` is the check.

## Releasing

Run `/release` from this repo. It bumps the version, writes the CHANGELOG entry, commits, opens a PR, and asks before merging. It never bumps and merges without showing you the diff first.

The `## Release Files` block below is the single source of truth for what a release rewrites — `/release` reads it rather than hard-coding paths. Add an entry of a **supported `kind`** and the next release picks it up; the supported kinds are exactly `json`, `changelog`, and `regex`, because those are the three `/release` step 3 implements. An entry of any other kind is not a graceful no-op — `/release` has no instruction for it and no instruction to refuse it, so add the handling to step 3 in the same change.

**Who sees an unbumped edit depends on how the marketplace was added**, and the two cases are opposite:

- **`directory` source** (how this repo is installed locally, pointing at this working tree) — Claude Code loads the plugin *from the source tree*. An edit is live in the next session with no bump at all. The version-keyed copy under `<config>/plugins/cache/agent-toolkit/` is written at install time and then never read; do not diff against it to check whether a change landed.
- **`github` source** (how anyone else installs this) — the cached payload is what gets served, and `claude plugin update` is a no-op unless the version string changed. For them, the bump *is* the delivery mechanism.

So local dogfooding never needs a release, and shipping to anyone else always does.

### `git checkout` is the deployment command

This follows from the above and is worth stating on its own, because it is the most
surprising property of this repo:

**Whatever branch is checked out here is what every session in both profiles loads** —
including sessions in unrelated projects, since the plugin is installed at *user*
scope. Not the tagged version, not `main`, not the cache: the bytes on disk at
`plugins/agent-toolkit/skills/…` at the moment a session starts. Uncommitted edits
count too; it tracks the filesystem, not git.

Verified 2026-08-06: with `test/release-dry` checked out, a probe session in each
profile loaded skill text byte-identical to that branch's tip and differing from
`main` by 28 lines, while `claude plugin list` reported a clean `0.1.0` in both.

Three consequences:

- **The version string tells you nothing about what is loaded.** `installed_plugins.json`
  pinned `0.1.0` and `gitCommitSha: cd966c7` while `HEAD` was several commits past it.
  To know what a session is running, check `git rev-parse HEAD` here — not the version.
- **Don't leave an experimental branch checked out.** Return to `main` when you stop
  working on a branch, or you are silently running unreviewed skill text everywhere.
- **A session cannot see its own edits to a skill.** Skill text is resolved once, at
  session start, and cached in the running process; the `Skill` tool does not re-read
  the tree when it fires. A session that was already running when you edited a skill
  keeps serving the old text for the rest of its life, and nothing in its behaviour
  signals that it is stale. Subagents inherit the snapshot of the `claude` process
  hosting them, so spawning a fresh one to check an edit returns its parent's text,
  not the tree's — there is no way to verify the edit from inside. Read "an edit is
  live in the next session" literally: **the next session**, meaning a `claude`
  process started after the edit. Here the natural one is a worker spawned into a
  fresh cmux tab by the very skill under test.

  Verified 2026-08-09: `SKILL.md` was edited at 13:57:41. A subagent inside a `claude`
  process started at 13:16:29 was served the pre-edit text — all seven markers checked
  were absent and the served text matched the pre-edit content exactly — while a
  worker spawned into a fresh cmux tab at 14:10:31 was served the post-edit text, all
  seven markers present. Same working tree, same `directory`-source install, same
  moment; the only variable was process start time.

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
    pattern: '(?m)^Latest: \*\*v(?P<version>\d+\.\d+\.\d+) — "(?P<codename>[^"]+)"\*\* \((?P<summary>.*)\)$'
    replace: 'Latest: **v{version} — "{codename}"** ({summary})'
```

Three properties of that pattern are load-bearing, and each replaces a version that
was wrong:

- **Python `re` syntax — `(?P<name>…)`, not `(?<name>…)`.** `python3` is this repo's
  only scripting dependency, and it is what a release will reach for. Python rejects
  the .NET/JS spelling outright (`re.error: unknown extension ?<v`), so a pattern
  written that way cannot be executed at all — and step 3's "if it does not match,
  stop" turns that into a *refused* release rather than a visibly broken one.
- **Anchored `^…$` under `(?m)`.** Without anchors, the greedy `(?P<summary>.*)` runs
  to the last `)` anywhere on the line, so any text following the summary is captured
  and then silently dropped by the rewrite. It matched, so nothing refused — a false
  document produced quietly. The anchors make "something follows the summary" a
  non-match, which step 3 does refuse.
- **It captures every field that goes stale, not just the digits.** An earlier version
  captured only the version, so a release bumped the number and left the codename and
  summary describing the *previous* release.

Values come from step 2 of `/release`: `version` from the bump, `codename` from the
release, and `summary` newly written to describe *this* release — it is authored, not
carried over from the captured group.

The plugin version and the marketplace entry's version must always agree — `claude plugin validate .` fails when they drift.

## Commit Convention

This repo follows [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/), enforced by `.git/hooks/commit-msg`.

**Subject** — `<type>(<scope>): <description>`, ≤ 72 characters.

- **Type** — one of `feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert`.
- **Scope** — the primary touched area (e.g., `skills/spawn-agent`, `skills/spawn-agent/herdr`, `marketplace`, `docs`).
- **Breaking change** — append `!`, or use a `BREAKING CHANGE:` body footer.

These types drive the bump `/release` proposes. The mapping lives in that skill's step 2 and is **not** duplicated here — two copies drifted apart once already (this file said `!` → major with no pre-1.0 clause while the skill said minor below 1.0.0, so the two documents proposed different versions for the same commits).

**Release commits** carry a footer:

```
Release: vX.Y.Z "Codename"
```
