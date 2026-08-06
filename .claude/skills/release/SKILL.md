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
git status --porcelain                          # A. must be empty
git rev-parse --abbrev-ref HEAD                 # B. must NOT be main
git log ${LAST:+$LAST..}HEAD --oneline          # C. must be non-empty
gh auth status                                  # D. must be logged in
claude plugin validate . && claude plugin validate ./plugins/agent-toolkit   # E.
```

Report **every** failure at once, then refuse. Precedence when several fire:

| # | Failure | Refuse with |
| --- | --- | --- |
| **C** | No commits since `$LAST` | *"nothing to release since `<tag>`"* — **this one wins outright.** It is terminal: there is no release to cut, so do not offer a branch, a version, or anything else. |
| A | Dirty tree | *"commit or stash first"* — a release commit must contain only the release files. |
| B | On `main` | Offer `git checkout -b release/vX.Y.Z` once the version is known (step 2). The PR needs a branch to come from. Any branch name works; `release/…` is a convention, not a gate. |
| D | Not authenticated | *"`gh auth login` first"* — only blocks step 6, so you may proceed to the diff and stop there if the user asks. |
| E | A manifest fails validation | Refuse. Shipping a broken manifest is worse than not shipping. |

**B and C together are the common case, and they contradict each other** — B says
branch and carry on, C says stop. C wins. Never offer a branch for a release that
has no commits in it.

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
| any `!` suffix or `BREAKING CHANGE:` footer | major — **but see the pre-1.0 rule below** |
| any `feat:` / `feat(...):` | minor |
| only `fix` / `perf` / `refactor` / `docs` / `chore` / `test` / `ci` / `style` / `build` / `revert` | patch |
| nothing at all | refuse — already caught by pre-flight C |

**Pre-1.0 override.** While the version is below `1.0.0`, a breaking change bumps the
**minor**, not the major (0.1.0 → 0.2.0) — semver's pre-1.0 clause. This overrides
row 1 of the table, so say which rule you applied when you propose the version
instead of appearing to contradict it.

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
  | anything that removes a capability (regardless of type) | Removed |

  A `!` / `BREAKING CHANGE:` commit keeps its type's section and is additionally
  called out in the entry's opening paragraph.
- **`regex`** — substitute **every** named group in `pattern` into `replace`, not
  just `version`. A pattern that names `codename` or `summary` expects those to be
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
git status --porcelain          # gate: no ' M' and no '??' lines may remain
```

**Check before committing, not after.** Any ` M` (modified, unstaged) or `??`
(untracked) line means step 3 touched something step 5 did not stage — refuse here,
while the tree is still recoverable. Verifying after the commit detects the same
fault one step too late: the half-applied release has already landed.

Only once that is clean:

```bash
git commit -m "chore(release): vX.Y.Z" -m "<one-line summary>" -m "Release: vX.Y.Z \"Codename\""
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
  refused one. The working tree is recoverable at every point before step 5.
