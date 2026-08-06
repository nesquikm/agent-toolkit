---
name: release
description: Release this marketplace — bump the version across every file in CLAUDE.md's Release Files block, write the CHANGELOG entry, commit, open a PR, and offer to merge it and refresh the local installs. Use when the user says "release", "ship it", "cut a version", or "/release".
argument-hint: '[major|minor|patch|X.Y.Z] [--codename "<name>"]'
---

# /release

Cut a release of this marketplace: bump → commit → PR → (ask) merge → refresh local installs.

Every step below shows the user what it is about to do and stops for approval **once**, at the diff. Nothing is pushed or merged without a separate explicit yes.

## Why the bump is load-bearing

Not for the local installs — those add this repo as a `directory` source and load the
plugin straight from the working tree, so every edit is already live. The bump is what
reaches **everyone installing from GitHub**: their payload is served from a
version-keyed cache, and `claude plugin update` returns `already at the latest version`
and keeps serving the old copy unless the version string changed.

The practical consequence for this skill: never conclude "the change is live, so the
release worked." It was live before you started.

## 1. Pre-flight

Refuse rather than guess. Run all four checks and report every failure at once:

```bash
cd "$(git rev-parse --show-toplevel)"
git status --porcelain                       # must be empty
git rev-parse --abbrev-ref HEAD              # must NOT be main
gh auth status                               # must be logged in
claude plugin validate . && claude plugin validate ./plugins/agent-toolkit
```

- **Dirty tree** — refuse. A release commit must contain only the release files;
  anything else belongs in its own commit first.
- **On `main`** — refuse, and offer `git checkout -b release/vX.Y.Z` once the version
  is known (step 2). The PR needs a branch to come from.
- **No commits since the last release** — refuse. `git log <last-tag>..HEAD --oneline`
  empty means there is nothing to ship.

## 2. Decide the version

Read the current version from `plugins/agent-toolkit/.claude-plugin/plugin.json`.

If the user passed an explicit `X.Y.Z`, use it. If they passed `major|minor|patch`,
apply that. Otherwise derive it from the Conventional Commit subjects since the last
release:

```bash
LAST=$(git tag --list 'v*' --sort=-v:refname | head -1)
git log ${LAST:+$LAST..}HEAD --format='%s%n%b'
```

| Found | Bump |
| --- | --- |
| any `!` suffix or `BREAKING CHANGE:` footer | major |
| any `feat:` / `feat(...):` | minor |
| only `fix` / `perf` / `refactor` / `docs` / `chore` / `test` / `ci` / `style` | patch |
| nothing at all | refuse — there is no release to cut |

Below 1.0.0, a `!` still bumps the **minor** (0.1.0 → 0.2.0), per semver's
pre-1.0 clause. Say so when you propose it, rather than silently doing something
different from the table.

Then pick a **codename** — one word, evocative of what shipped. Use `--codename` if
the user gave one; otherwise propose one and let the diff review be the approval.

## 3. Rewrite every file in the Release Files block

Read the `## Release Files` YAML block from `CLAUDE.md` and act on **every** entry —
do not hard-code the list here. That block is the contract; a file added there must
be picked up without editing this skill.

Per `kind`:

- **`json`** — set the dotted/indexed `field` to the new version. Preserve 2-space
  indent and the trailing newline; a reformat turns a one-line diff into a whole-file
  rewrite.
- **`changelog`** — insert a new section directly above the topmost existing `## [`
  heading. Never append at the end, and never touch an existing entry:

  ```markdown
  ## [X.Y.Z] — YYYY-MM-DD — "Codename"

  <one paragraph: what changed and why it mattered>

  ### Added / Changed / Fixed / Removed

  - **<lede>.** <what it does now, and what it did before if that is the point>
  ```

  Use only the subsections that have content. Get the date from `date +%F`, not from
  memory. Group by Conventional Commit type: `feat` → Added, `fix` → Fixed,
  `refactor`/`perf`/`style` → Changed, a removal → Removed.
- **`regex`** — the `pattern` carries a `(?<version>...)` named group; substitute the
  new version into `replace`. If the pattern does not match, **stop and say so** —
  do not invent a replacement line. An entry marked `optional: true` may be skipped
  when the file is absent, but never when it exists and simply fails to match.

## 4. Show the diff and get approval

```bash
git diff
```

Print it in full, then ask once:

> Release v`X.Y.Z` "Codename" — apply, commit, and open a PR? (y/n)

Anything other than `y`/`yes` aborts. Leave the working tree exactly as it is on
abort — the user may want to hand-edit the CHANGELOG prose and re-run.

## 5. Commit

Stage each Release Files path explicitly. `git add -A` is forbidden — it is the one
thing that could sweep an unrelated file into a release commit.

```bash
git add plugins/agent-toolkit/.claude-plugin/plugin.json .claude-plugin/marketplace.json CHANGELOG.md README.md
git commit -m "chore(release): vX.Y.Z" -m "<one-line summary>" -m "Release: vX.Y.Z \"Codename\""
```

The `commit-msg` hook enforces the subject format; if it rejects, fix the message
rather than bypassing with `--no-verify`.

## 6. Push and open the PR

```bash
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
gh pr create --title "chore(release): vX.Y.Z \"Codename\"" --body "<the CHANGELOG entry body>"
```

Report the PR URL. Then ask, separately — this is a **second** approval, never folded
into the one from step 4:

> PR is open: `<url>`. Merge it? (y/n)

## 7. Merge, tag, and refresh the local installs

Only on an explicit yes:

```bash
gh pr merge --squash --delete-branch
git checkout main && git pull
git tag "vX.Y.Z" && git push origin "vX.Y.Z"
```

The tag matters beyond bookkeeping: step 2 reads `git tag --list 'v*'` to find what
"since the last release" means. Skip it and the next release derives its bump from
the whole history.

Then refresh both profiles. The skills were already live (directory source), but this
re-syncs the recorded version so `claude plugin list` stops reporting the old one:

```bash
for CFG in "$HOME/.claude" "$HOME/.claude-st"; do
  CLAUDE_CONFIG_DIR="$CFG" claude plugin marketplace update agent-toolkit
  CLAUDE_CONFIG_DIR="$CFG" claude plugin update agent-toolkit@agent-toolkit --scope user
done
```

**Always prefix `CLAUDE_CONFIG_DIR` explicitly.** A shell `chpwd` hook that switches
profiles per directory is a common setup, and it makes a bare `claude plugin ...`
silently target whichever profile the current directory selects.

Finish by telling the user that **running sessions keep the old cached payload** —
the new version reaches them on restart, not before.

## Rules

- One approval for the content (step 4), a second for the merge (step 6). Never one
  for both.
- Read the file list from `CLAUDE.md`; never hard-code it in this skill.
- Never `git add -A`, never `--no-verify`, never force-push.
- If any step fails, stop and report where — a half-applied release is worse than a
  refused one. The working tree is recoverable at every point before step 5.
