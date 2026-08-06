---
name: release
description: Release this marketplace — bump the version across every file in CLAUDE.md's Release Files block, write the CHANGELOG entry, commit, open a PR, and offer to merge it and refresh the local installs. Use when the user says "release", "ship it", "cut a version", or "/release".
argument-hint: '[major|minor|patch|X.Y.Z] [--codename "<name>"]'
---

# /release

Cut a release of this marketplace: bump → commit → PR → (ask) merge → refresh local installs.

This asks for approval **twice**, and the two are never merged into one: once at the
diff, before anything is committed, and again at the open PR, before it is merged.
A yes to the first is not a yes to the second.

## Why the bump is load-bearing

Not for the local installs — those add this repo as a `directory` source and load the
plugin straight from the working tree, so every edit is already live. The bump is what
reaches **everyone installing from GitHub**: their payload is served from a
version-keyed cache, and `claude plugin update` returns `already at the latest version`
and keeps serving the old copy unless the version string changed.

The practical consequence for this skill: never conclude "the change is live, so the
release worked." It was live before you started.

## 1. Pre-flight

Refuse rather than guess. Every check is in the block — nothing hides in the prose:

```bash
cd "$(git rev-parse --show-toplevel)"
LAST=$(git tag --list 'v*' --sort=-v:refname | head -1)
git status --porcelain                          # A/A′. empty, or only Release Files paths
git rev-parse --abbrev-ref HEAD                 # B.  should not be main
git log ${LAST:+$LAST..}HEAD --oneline          # C.  must be non-empty
gh auth status                                  # D.  should be logged in
claude plugin validate . && claude plugin validate ./plugins/agent-toolkit   # E.
```

Run all five, then read the table below — **not every failure is a refusal**, and
which ones are is the whole point of it:

| # | Failure | Then |
| --- | --- | --- |
| **C** | No commits since `$LAST` | **Terminal, and it beats everything below.** Refuse with *"nothing to release since `<tag>`"*. Do not offer a branch, a version, or a diff. |
| A | Dirty tree — **outside** the Release Files paths | Refuse: *"commit or stash first"*. A release commit must contain only the release files. |
| A′ | Dirty tree — **only** Release Files paths | **Not a refusal.** This is what an aborted earlier run leaves behind (step 4 says n → tree left rewritten). Say so, offer `git checkout -- <those paths>` to start clean, and continue on yes. Step 3 overwrites them anyway; the danger is only that stale edits get read as this release's content. |
| B | On `main` | Not terminal. Once the version is known (step 2), offer `git checkout -b release/vX.Y.Z`. Any branch name works — `release/…` is convention, not a gate. |
| D | Not authenticated | Not terminal. Report it now, continue through the diff, and **stop before step 6** — pushing is the first thing that needs `gh`. Do not discover this after the user has approved a diff. |
| E | A manifest fails validation | Refuse. Shipping a broken manifest is worse than not shipping. |

Report every failure at once, then act on the strictest: **C refuses outright, and A
and E refuse; A′, B and D are conditions you carry forward, not stops.** B and C look
like they conflict — B says branch and carry on, C says stop — but C wins: never offer
a branch for a release that has no commits in it.

## 2. Decide the version

Read the current version from the **first `kind: json` entry in the Release Files
block** — not from a path written here. Today that is
`plugins/agent-toolkit/.claude-plugin/plugin.json`; if the block moves it, this step
must follow, and a path hard-coded in this file would silently read the old one while
step 3 rewrote the new one.

If the user passed an explicit `X.Y.Z`, use it. If they passed `major|minor|patch`,
apply that. Otherwise derive it from the Conventional Commit subjects since the last
release:

```bash
LAST=$(git tag --list 'v*' --sort=-v:refname | head -1)
git log ${LAST:+$LAST..}HEAD --format='%s%n%b'
```

| Found | Bump |
| --- | --- |
| any `!` suffix or `BREAKING CHANGE:` footer | major — **but see the pre-1.0 rule below** |
| any `feat:` / `feat(...):` | minor |
| only `fix` / `perf` / `refactor` / `docs` / `chore` / `test` / `ci` / `style` / `build` / `revert` | patch |
| nothing at all | refuse — already caught by pre-flight C |

**Pre-1.0 override.** While the version is below `1.0.0`, a breaking change bumps the
**minor**, not the major (0.1.0 → 0.2.0) — semver's pre-1.0 clause. This overrides
row 1 of the table, so say which rule you applied when you propose the version
instead of appearing to contradict it.

**Then check the version is actually free**, before anything is rewritten:

```bash
git tag --list "v$NEW"          # must be empty
```

A tag that already exists does not fail until `git tag` in step 7 — which runs
*after* `gh pr merge`. The release would land merged and untagged, and step 7 explains
exactly why that poisons the next release's "since the last release" window. Refuse
here instead, where nothing has been written. This bites hardest on an explicit
`X.Y.Z` argument, which no other check validates.

Then pick a **codename** — one word, evocative of what shipped. Use `--codename` if
the user gave one; otherwise propose one. It reaches both the CHANGELOG heading and
the README's `Latest:` line, so the diff in step 4 shows its full effect and the
review of that diff is its approval.

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
  memory. Every Conventional Commit type in `CLAUDE.md` has a bucket — there is no
  "use your judgement" case:

  | Type | Section |
  | --- | --- |
  | `feat` | Added |
  | `fix` | Fixed |
  | `refactor`, `perf`, `style`, `docs`, `build`, `ci`, `chore`, `test`, `revert` | Changed |
  | a commit that removes a **capability users had** | Removed — this row wins over the type rows |

  Precedence, so the last row is not a judgement call dressed as a rule: `Removed` is
  for a capability the user can no longer reach. Deleting a *broken or superseded
  implementation* of something that still works is `Fixed` or `Changed` by its type —
  retiring one of two ways to wait on a worker is not a removal if waiting still works.
  A `!` / `BREAKING CHANGE:` commit keeps its type's section and is additionally
  called out in the entry's opening paragraph.
- **`regex`** — fill **every** named group in `replace`, not just `version`, and fill
  them from the *release*, not from the captured text: `version` from the bump,
  `codename` from step 2, and `summary` a **newly written** one-line description of
  this release. Copying the captured groups straight back is an identity transform
  that changes nothing. A pattern that names `codename` or `summary` expects those to be
  rewritten too; leaving them is how a README ends up claiming the new version
  shipped the previous version's contents. If the pattern does not match, **stop and
  say so** — do not invent a replacement line. An entry marked `optional: true` may
  be skipped when the file is absent, but never when it exists and fails to match.

**Then re-validate**, before showing anything:

```bash
claude plugin validate . && claude plugin validate ./plugins/agent-toolkit
```

Pre-flight validated the manifests *before* you rewrote them; this validates what you
actually produced. A `json` rewrite that broke a manifest is exactly the failure the
indent-and-newline warning above exists to prevent, and only this check catches it.

## 4. Show the diff and get approval

```bash
git diff
```

Print it in full, then ask once:

> Release v`X.Y.Z` "Codename" — apply, commit, and open a PR? (y/n)

Anything other than `y`/`yes` aborts. Leave the working tree exactly as it is on
abort — the user may want to hand-edit the CHANGELOG prose and re-run.

## 5. Commit

Stage exactly the paths from the `## Release Files` block you read in step 3 — the
same list, not a list you retype here. `git add -A` is forbidden: it is the one thing
that could sweep an unrelated file into a release commit.

```bash
git add <every path from the Release Files block, one per entry you rewrote>
```

Then gate on **two** questions, both before committing:

```bash
git diff --cached --name-only | sort          # what is actually staged
git status --porcelain                        # what state everything is in
```

1. **Is every Release Files path staged?** Compare the first list against the paths
   you parsed in step 3. A path in the block but missing here was never rewritten —
   which is exactly what a `regex` entry that failed to match, or a `kind` step 3
   does not implement, produces. Refuse.
2. **Is anything modified but not staged?** In `git status --porcelain` the status is
   two columns — index, then worktree. Refuse on **any line whose second column is
   not a space**, and on `??`. Do not scan for the literal string `' M'`: a file
   staged and then edited again reads `MM`, whose second column is `M` while the
   line contains no `' M'` at all — it would slip through and commit content that
   differs from what step 3 produced.

Both checks run *before* `git commit`, while the tree is still recoverable. Checking
afterwards detects the same faults one step too late — the half-applied release has
already landed.

The first check is the important one and the one an earlier version lacked: a gate
that only looks for leftovers is blind to an entry that was never touched, and
"never touched" is the failure mode a broken `regex` entry actually causes.

Only once both are clean:

```bash
git commit -m "chore(release): vX.Y.Z" -m "<the same summary written for the README Latest: line>" -m "Release: vX.Y.Z \"Codename\""
```

An earlier version of this skill hard-coded four paths here while step 3 read the
block. They matched by coincidence; the moment a fifth entry was added, step 3 would
rewrite it and step 5 would leave it behind — a half-applied release, which the last
Rule calls worse than a refused one. The check above is what makes that impossible
rather than merely discouraged.

The `commit-msg` hook enforces the subject format; if it rejects, fix the message
rather than bypassing with `--no-verify`.

## 6. Push and open the PR

```bash
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
gh pr create --base main \
  --title "chore(release): vX.Y.Z \"Codename\"" \
  --body "<the new CHANGELOG section: its opening paragraph AND every ### bullet, up to the next '## ['>"
```

`--base main` is explicit on purpose: `gh pr create` otherwise inherits the repo's
default branch, which is right here and silently wrong on any repo whose default is
not the release target.

**The PR is not the diff you just showed.** Step 4 showed only the release rewrite —
the handful of Release Files. This PR carries the whole `$LAST..HEAD` window, every
feature commit on the branch. Those are different by an order of magnitude, and the
approval below is the one that actually ships them, so show the real scope first:

```bash
git --no-pager diff --stat main...HEAD          # the real scope, as a stat
gh pr view --json commits,files --jq '"commits=\(.commits|length) files=\(.files|length)"'
```

**Not `gh pr diff --stat`** — `gh pr diff` has no `--stat`; it takes `--patch`,
`--name-only`, `--color` and `--exclude`, and passing `--stat` prints its help text
instead of failing, so the step looks like it ran and shows you nothing. The
three-dot `main...HEAD` is deliberate: it diffs against the merge base, which is what
the PR actually contains, where `main..HEAD` would mislead the moment `main` moves.

Then ask, separately — this is a **second** approval, never folded into step 4's:

> PR is open: `<url>`.
> It merges `<N>` commits / `<F>` files changed — not just the version bump you approved.
> Merge it? (y/n)

Treat **no answer** exactly as `n`: stop and report the state below. Never merge on
silence, a timeout, or a background event — only on an explicit yes from the user.

### If the answer is no

Stop, and say plainly what state the repo is in — this is the one point with no clean
abort, and the next run will misbehave if it is treated as a fresh start:

- the version is **already bumped and committed** on this branch, and pushed;
- pre-flight C still measures from the **old** tag, so a re-run re-derives the bump
  from commits that are already in the open PR and proposes a *second* increment;
- step 3 would insert a **second** CHANGELOG entry above the first.

So do not re-run `/release` on this branch. Offer exactly three exits:

| Want | Do |
| --- | --- |
| Merge later, unchanged | Nothing. Merge the PR by hand; then run step 7's tag + refresh. |
| Change the release content | Amend on this branch (`git commit --amend` or a fixup), force-push **only** if the user asks — the Rules forbid it otherwise — and re-review the PR. Do not re-run this skill. |
| Abandon the release | `gh pr close <url>`, `git checkout main`, `git branch -D <branch>`, and delete the remote branch. Only then is a fresh `/release` correct. |

Whichever they pick, **return to `main` afterwards** — a directory-source marketplace
serves whatever branch is checked out to every session in both profiles.

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

Finish by telling the user that **already-running sessions keep the skill text they
loaded at start** — not because of the cache (a directory source never reads it), but
because a session's skill inventory is built once. New sessions get the new text
immediately; existing ones need a restart.

## Rules

- One approval for the content (step 4), a second for the merge (step 6). Never one
  for both.
- Read the file list from `CLAUDE.md`; never hard-code it in this skill.
- Never `git add -A`, never `--no-verify`, never force-push.
- If any step fails, stop and report where — a half-applied release is worse than a
  refused one. The working tree is recoverable up to and including step 5's gate —
  the first irreversible act is `git commit`, and everything before it can be undone
  with `git checkout -- <paths>`.
