---
name: cmux-spawn-agent-smoke
description: Smoke-test the cmux-spawn-agent skill on this machine — a bounded, evidence-first run that proves a worker really spawns into a cmux surface, becomes addressable, takes a task, replies, and is torn down without leaks. Use when the user says "smoke test", "self-test", "does spawning still work", "verify the plugin", or runs /agent-toolkit:cmux-spawn-agent-smoke, and after installing or upgrading agent-toolkit or when a spawn run behaved oddly. Not for ordinary work — a request to spawn agents in order to get something done belongs to cmux-spawn-agent instead.
---

# Smoke-test `cmux-spawn-agent`

Ten checks, one split, one worker, about five minutes. It answers one question:
**does this plugin work on this machine right now** — after an install, after an
upgrade, or when a run has started feeling wrong.

Twelve numbered sections below — ten of them checks, plus the rails at 6 and an
optional extra at 10.

It is not an audit. It does not read the skill for correctness; it makes the
machine prove the handful of things that have actually broken here, and that
reading cannot settle. Every check below names the signal you look at and what
its failure means, because in this plugin several unrelated faults share one
signature and "it timed out" is not a diagnosis.

**Record as you go.** One row per check — number, PASS or FAIL, and the literal
output you based it on. Do not batch the verdict to the end; a run that dies at
check 8 still has seven results worth reporting.

**Three attempts, then move on.** The auto-mode classifier denies calls that would
have worked — `cmux new-split` was denied twice in a row and succeeded on the third
identical attempt. A denial never reaches the shell, so it looks exactly like a real
failure. Reissue the same `Bash` call up to three times before recording FAIL, and
record *denied* rather than *failed* when that is what it was.

**One command per `Bash` call.** Same reason as the skill under test — a long
compound command is denied as a unit and denials escalate. The blocks here are
grouped for reading.

## 0. Staleness gate — first, and there is no point running anything else

**Skill text is resolved once, at session start, and cached for the life of the
process.** A session that started before the plugin changed is running the old text
and will smoke-test bytes nobody ships. Subagents inherit their parent's snapshot, so
re-running this inside a `Task` refreshes nothing.

Only `SKILL.md` is snapshotted. `peer.py`, `surface.py` and `watch-workers.py` are
read from disk on every invocation, so their mtimes are deliberately excluded below —
including them would fail sessions that are perfectly current.

```bash
python3 -c '
import json, os, subprocess, sys, time

def ps(fmt, pid):
    r = subprocess.run(["ps", "-o", fmt, "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip()

root = sys.argv[1]
pid = int(sys.argv[2])
for _ in range(8):
    argv0 = (ps("command=", pid).split() or [""])[0]
    if os.path.basename(argv0).startswith("claude"):
        break
    parent = ps("ppid=", pid)
    if not parent or int(parent) <= 1:
        sys.exit("no claude ancestor above pid %s -- check the session start time by hand" % sys.argv[2])
    pid = int(parent)
else:
    sys.exit("no claude ancestor within 8 levels of pid %s" % sys.argv[2])

texts = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(root, "skills"))
         for f in fs if f == "SKILL.md"]
newest, newest_path = max((os.path.getmtime(p), p) for p in texts)
started = time.mktime(time.strptime(ps("lstart=", pid)))
print("plugin root  :", root)
print("version      :", json.load(open(os.path.join(root, ".claude-plugin", "plugin.json")))["version"])
print("newest SKILL :", time.ctime(newest), "->", os.path.basename(os.path.dirname(newest_path)))
print("session      :", pid, "started", time.ctime(started))
print("GATE:", "PASS - session postdates the skill text" if started > newest
      else "FAIL - STALE SESSION, start a fresh claude before smoke-testing")
' "${CLAUDE_PLUGIN_ROOT}" $$
```

The first argument is already an absolute path by the time you read this — it is the
root the plugin was actually **loaded** from, which is the only one that matters.
Under a `directory`-source marketplace that is the working tree; under a `github`
source it is the version-keyed cache. Do not substitute a path you found yourself.

**It walks the process tree instead of taking the parent of `$$`, and that is not
tidiness.** The one-liner elsewhere in this plugin reads `ps -o ppid= -p $$`, which is
right only when the block is pasted straight into a `Bash` call. Run through any extra
shell layer it names that layer's parent — a process born seconds ago, which is
*newer* than any edit, so the gate reports PASS for a session that is arbitrarily
stale. Measured while writing this file — through one wrapping `zsh` the naive form
resolved a pid 2 h 38 m younger than the real session and flipped a genuine FAIL to
PASS, while the form above returned the same `claude` pid at zero, one and two extra
levels of shell. A gate that fails open is worse than no gate.

**Keep the pid it prints.** Check 1 needs it.

**On FAIL, stop.** Open a new cmux tab yourself and run
`/agent-toolkit:cmux-spawn-agent-smoke` there — a `claude` started after the edit is
the only thing that loads it. Two notes on the alternatives:

- Quitting and relaunching in **this** tab works too, and inherits this surface's
  ledger file, because the ledger is keyed by the cmux surface uuid and that outlives
  any one `claude`. Check 2 is where that shows up.
- Having a *spawned worker* run this procedure also works — the worker is a new
  `claude` with a current snapshot. But a stale supervisor is following stale spawn
  instructions to create it, so report that the tab was opened under old text.

This gate is not theoretical. Writing this file on 2026-08-09, the authoring session
had started at 13:16:29 and the newest `SKILL.md` was written at 15:42:13 — a session
two and a half hours behind the text it would have claimed to test.

## 1. Preflight — five things, none of which cost anything

```bash
[ -n "$CMUX_WORKSPACE_ID" ] && [ -n "$CMUX_SURFACE_ID" ] \
  && echo "PASS in a cmux terminal" || echo "FAIL not in a cmux terminal"
```

Both ids, not one: placement is anchored on the surface and the ledger is keyed by
it. **FAIL means stop** — nothing below this line can run outside cmux.

```bash
cmux --json identify
```

PASS if it returns JSON with a `caller` block. That block is where you are; `focused`
is wherever the user has drifted to, and the two routinely differ — they did while
this was written (`caller` in `workspace:3`, `focused` in `workspace:1`). Never place
anything by `focused`.

```bash
claude --help | grep -- '-n, --name'
```

PASS on `-n, --name <name>  Set a display name for this session`. The name is the
only join key this plugin has; without the flag nothing downstream works, and the
failure would surface much later as a worker that never registers under the name you
expect.

```bash
CLPID="<the session pid check 0 printed>"
ps -o command= -p "$CLPID" \
  | grep -qE -- '--dangerously-skip-permissions|--permission-mode[= ]bypassPermissions' \
  && echo "FAIL bypass class - messaging will be held in both directions" \
  || echo "PASS prompting class"
```

Take the pid from check 0 rather than deriving it again — for the reason check 0
gives, a second derivation is a second chance to inspect the wrong process, and here
that would clear a bypass session as safe.

A bypass-class supervisor is deaf and mute to peer messages by default — the task is
held at the worker and the reply is held here, each behind a dialog somebody has to
find. Checks 8 and 9 would then fail for a reason that has nothing to do with the
plugin. This grep only sees flags passed at launch; if the mode was changed in-session
the session's own status line is the authority (`⏵⏵ auto mode on`, and so on).

```bash
LEDGER="${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
[ -s "$LEDGER" ] && { echo "STOP this session already owns spawned workers:"; cat "$LEDGER"; } \
  || echo "PASS no live ledger for this surface"
```

**This is a rail, not a check.** A non-empty ledger means the session in this tab has
workers it has not finished with, and check 11 deletes the ledger and closes what it
lists. Run the smoke test in a different tab instead. Do not "just move it aside" —
the rows are the only record of which tabs that run may close.

## 2. The stale-ledger guard — three fixtures, no spawn

The ledger path is keyed by the surface uuid, so relaunching `claude` in the same cmux
tab reopens the previous run's file, rows and all, including rows in a shape this
version no longer writes. The watcher reads column 1 as a name; on a five-column
ledger it watches session uuids and matches nothing.

```bash
D="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
mkdir -p "$D"
: > "$D/empty.tsv"
printf 'a\tb\tc\td\n' > "$D/ok4.tsv"
printf 'a\tb\tc\td\te\n' > "$D/legacy5.tsv"
```

```bash
D="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
for f in empty ok4 legacy5; do
  awk -F'\t' 'NF && NF!=4 {print FILENAME": "NR" columns="NF; bad=1} END{exit bad}' "$D/$f.tsv"
  echo "$f -> exit=$?"
done
```

PASS on exactly `empty -> exit=0`, `ok4 -> exit=0`, and `legacy5 -> exit=1` preceded
by a `columns=5` line naming the file. Measured in that form on 2026-08-09.

**Fixtures, never the real ledger.** An empty file must pass — the anchor block
`touch`es one before the first row exists — and a five-column file must be refused
loudly, because the alternative is a watcher that runs happily and reports nothing for
the whole run.

## 3. The name-collision guard — it must ask for `name`, not for the address

A name collision mis-delivers a task: `peer.py` hands back the wrong socket and the
watcher reports the wrong worker. The guard is only worth anything if it sees names
that are in use but unreachable, which on this machine is most of them.

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"
VICTIM=$(python3 -c '
import glob, json, os, sys
roots = [d for d in os.environ.get("CLAUDE_CONFIG_DIR", "").split(":") if d] or [os.path.expanduser("~/.claude")]
for d in roots:
    for p in glob.glob(os.path.join(d, "sessions", "*.json")):
        try:
            r = json.load(open(p))
        except (OSError, ValueError):
            continue
        if r.get("messagingSocketPath") or not r.get("name"):
            continue
        pid = r.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except PermissionError:
            pass
        except OSError:
            continue
        print(r["name"])
        sys.exit(0)
')
echo "VICTIM=[$VICTIM]"
```

That liveness test is `peer.py`'s own, copied deliberately rather than written fresh.
The obvious shorthand — `os.kill(r.get("pid") or -1, 0)` — is the bug `peer.py` carries
a comment about: signal 0 to pid `-1` addresses every process the user may signal and
answers *yes*, so a record with no usable pid is classed alive and its name is handed
back as `VICTIM`. `peer.py` then correctly refuses that name, the block below records
FAIL, and the guard being blamed is the one behaving properly. A fixture picker that
disagrees with the script under test measures nothing but the disagreement.

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"
VICTIM="<paste the name printed above>"
python3 "$P" "$VICTIM" name; echo "  name    exit=$?"
python3 "$P" "$VICTIM";      echo "  address exit=$?"
python3 "$P" no-such-session-xyz name; echo "  absent  exit=$?"
```

PASS when the name form prints the name and exits 0, the address form prints nothing
and exits 1, and the absent name exits 1. That contrast **is** the check: the address
form would call a name that is very much in use free. Measured on this machine
2026-08-09 — 7 live sessions, 6 of them with no messaging socket, so the address form
declared 6 of 7 names in use to be available.

If `VICTIM` comes back empty, every live session here has a socket. Record the check
as SKIPPED with that reason and confirm the third line alone (an absent name exits 1);
do not invent a session to test against.

## 4. The prune, in both directions

Both halves of the documented one-liner are load-bearing and they fail in **opposite**
directions, so a run that only ever exercises one of them proves nothing.

```bash
D="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\n' > "$LEDGER"
su=SU-AAA
[ -n "$su" ] && { grep -v -F "$su" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "A: rows=$(wc -l < "$LEDGER" | tr -d ' ') tmp=$(ls "$LEDGER.tmp" 2>/dev/null | wc -l | tr -d ' ')"
```

PASS on `A: rows=0 tmp=0`. This is the last-row case, which on a one-worker run is the
only prune the run ever does — joined with `&&` instead of `;` the `mv` is skipped
(because `grep` exits 1 when it selects nothing), the ghost row survives, and a `.tmp`
is left behind. `tmp=1` is that bug.

```bash
D="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\nw2\tSU-BBB\tPU-BBB\treported\n' > "$LEDGER"
unset su
[ -n "$su" ] && { grep -v -F "$su" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "B: rows=$(wc -l < "$LEDGER" | tr -d ' ') bytes=$(wc -c < "$LEDGER" | tr -d ' ')"
```

PASS on `B: rows=2 bytes=52` — untouched. Unset `$su` is reachable by *following* the
skill rather than by ignoring it, since variables die with each `Bash` call and the
prune reads as a standalone command. Without the `[ -n "$su" ]` guard, `grep -v -F ""`
selects nothing and installs it over the whole file: `rows=0 bytes=0`, **exit 0**. The
destruction reports success, and what it destroys is the record of which surfaces the
run is allowed to close. Both directions measured 2026-08-09.

## 5. Arm a deaf watcher and wait for `WARN`

A watcher that matches nothing used to be indistinguishable from a healthy watcher
whose workers are still busy. Prove it now says so. This runs **concurrently** with
everything below — arm it, keep going, and collect its line later.

Name the scratch ledger with this surface's id so the leak proof in check 11 catches
this watcher too.

`Monitor` tool, `command`:

```bash
python3 -u "${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/watch-workers.py" \
    "${TMPDIR:-/tmp}/cmux-spawn-agent-smoke/warn-${CMUX_SURFACE_ID}.tsv" 1
```

Create its ledger first, with one row naming nobody:

```bash
D="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
mkdir -p "$D"
printf 'smoke-warn-nobody\tSU-X\tPU-X\tspawned\n' > "$D/warn-${CMUX_SURFACE_ID}.tsv"
```

PASS on exactly one line, about 30 seconds in:

```
WARN ledger has 1 row(s), none match a live session name: /…/warn-<surface-uuid>.tsv
```

Measured in that form on 2026-08-09. Note the shape of the assertion: **one** line,
naming the "rows match nothing" case, and no second one afterwards. Two `WARN`s, or a
`GONE`, or silence past a minute, are all FAIL. Silence in particular is the exact
regression this exists to catch.

Keep the task id. Check 11 stops it.

## 6. Rails for everything below this line

The remaining checks create and destroy a real surface. These are not advice:

- Spawn only into **the caller's own workspace**, `$CMUX_WORKSPACE_ID`, anchored on
  `$CMUX_SURFACE_ID`. Never into what is focused.
- **Close nothing that is not in this run's ledger**, however idle it looks.
- **Never close `$CMUX_SURFACE_ID`.** Assert it by hand before every close.
- Never `close-others`, `close-left`, `close-right`, or `close-workspace`. There is no
  case in this procedure where any of them is the right command, and each one closes
  tabs this run does not own.
- One split for the whole run.

## 7. One real spawn — follow `cmux-spawn-agent`, do not reimplement it

**Read `/agent-toolkit:cmux-spawn-agent` and follow its "Spawn one agent" section as
written.** The point of the check is that the shipped procedure works; a hand-rolled
launch tests your typing. Two deliberate substitutions, and no others:

- `NAME` is `smoke-$$` or anything else unlikely to collide.
- `REPO` is a **throwaway checkout whose path contains a space**, created below. That
  choice does two jobs at once. It exercises the quoting fix — an unquoted path dies
  at `cd` before `claude` ever starts, and zsh's message for it is the unhelpful
  `string not in pwd`. And it gives the cwd verification power it does not otherwise
  have — a new split inherits the caller's cwd, so when `REPO` *is* the caller's cwd
  the check cannot fail and therefore proves nothing.

```bash
REPO="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke/smoke repo"
mkdir -p "$REPO"
git -C "$REPO" init -q
[ -d "$REPO/.git" ] && echo "PASS scratch repo: $REPO"
```

Record the baseline before you split:

```bash
cmux --json --id-format both tree --workspace "$CMUX_WORKSPACE_ID" | python3 -c '
import json, sys
panes, surfaces = set(), set()
def walk(n):
    if isinstance(n, dict):
        ref = str(n.get("ref", ""))
        if ref.startswith("pane:"): panes.add(n.get("id"))
        if ref.startswith("surface:"): surfaces.add(n.get("id"))
        for v in n.values(): walk(v)
    elif isinstance(n, list):
        for v in n: walk(v)
walk(json.load(sys.stdin)["windows"])
print(len(panes), "panes,", len(surfaces), "surfaces")'
```

Then follow the skill: collision check, `new-split right --surface "$CMUX_SURFACE_ID"`,
resolve, **write the ledger row**, arm the run's watcher, launch, wait for addressable.

### 7a. The surface is new, and it is not yours

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/surface.py"
SURF="<the surface:N the split printed>"
echo "worker pane: $(python3 "$S" "$SURF" pane_id)"
echo "caller pane: $(python3 "$S" "$CMUX_SURFACE_ID" pane_id)"
```

PASS when both resolve and the two uuids **differ**, and the topology count above has
gone up by one pane and one surface. Equal pane uuids means the worker was placed in
the caller's own pane, where it becomes the selected tab and covers the session the
user is talking to — the one placement outcome that is worse than failing.

### 7b. The row is on disk before anything is launched

```bash
LEDGER="${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
awk -F'\t' '{print NR": "NF" cols: "$0}' "$LEDGER"
```

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"
python3 "$P" "<NAME>" name; echo "registry exit=$?"
```

PASS when the ledger holds exactly one four-column row naming your worker **and** the
registry lookup exits 1. That pair is the whole ordering guarantee: a recorded surface
that is not yet running anything. Reversed, the window between the two holds a live
agent no ledger knows about, and a turn that dies inside it orphans the tab forever.

`awk: can't open file …` means no row was written at all — the launch happened without
a ledger, or the path is not the one the anchor block built. Either is a FAIL here.

Two blank-looking fields in that row is a FAIL, not cosmetics — tab is IFS whitespace,
so the cleanup loop's `read` shifts every column left and the row can never be offered
for closing.

### 7c. Read the launch string before you send it

The `cd` is the single most-dropped part of the launch line, it sits mid-string, and a
supervisor that had just read the warning dropped it on the very next line it wrote.
Before sending, confirm your `cmux send` payload literally contains
`cd \"$REPO\" && claude -n <NAME>` — escaped double quotes, not single ones, not bare.
Record the string you sent.

### 7d. Readiness, and the two failures that look identical

Run the skill's readiness loop, but bound it at 20 rather than 60 — you are expecting
a gate here, not hoping to avoid one:

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"
n=0
until python3 "$P" "<NAME>" >/dev/null; do
  sleep 1
  n=$((n+1))
  [ "$n" -gt 20 ] && { echo "not addressable after ${n}s"; break; }
done
echo "loop ended at n=$n"
```

A worker that never registers costs 21.8 s of wall clock there, measured — so the
bound is the difference between a check and a stall, and 20 is chosen to reach the
screen read quickly, not to be generous.

If it exits early, PASS, and skip to check 8. If it hits the bound, **read the screen —
this is mandatory and it is the whole point of the check:**

```bash
cmux read-screen --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>"
```

No worker signal fires during this window. `peer.py` exits 1; `ASK`, `ATTN` and `GONE`
are all polled from a registry record that does not exist yet. The screen is the only
place the reason is written, and there are two of them:

| What the screen shows | What it means | What to do |
| --- | --- | --- |
| a shell prompt, `zsh:cd:1: string not in pwd: …` above it | the launch line lost its quoting and died at `cd`; `claude` never started | FAIL check 7c. The payload was wrong, not the plugin |
| `Quick safety check: Is this a project you created or one you trust?` | the folder-trust gate — expected here, since the scratch repo is new to this profile | clear it, below, and count it as PASS |
| a Claude prompt, no dialog | it booted but has not registered yet | re-run the loop once with a 40 bound before recording FAIL |

The gate is answered by `enter` **alone**, because option 1 is already selected:

```bash
cmux send-key --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>" enter
```

**Do not send `down` first.** On the plain variant of that dialog option 2 is
"No, exit", so the reflexive rescue sequence terminates the worker it was meant to
save. Count the `❯` row in the output you just read; if it is already on option 1,
`enter` is the entire answer. Then re-run the readiness loop.

Then verify where it landed. Compare the two as **paths, not as strings**:

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"
REPO="${TMPDIR:-/tmp}/cmux-spawn-agent-smoke/smoke repo"
python3 -c '
import os, sys
want, got = sys.argv[1], sys.argv[2]
print("want:", want)
print("got :", got or "(nothing)")
if not got:
    sys.exit("FAIL peer.py printed no cwd -- the worker is not registered")
print("PASS" if os.path.exists(got) and os.path.samefile(want, got) else "FAIL")
' "$REPO" "$(python3 "$P" "<NAME>" cwd)"
```

PASS on `PASS`, with both paths printed so the row you record carries its own evidence.
A FAIL here — the caller's cwd, a truncation at the space — is a FAIL of the quoting,
and it is only visible at all because `REPO` was chosen to differ from the caller's cwd.

**Do not assert the two strings are equal.** On macOS a perfectly healthy run prints
two visibly different paths, for two independent reasons that both apply at once:
`$TMPDIR` ends in a slash, so the literal carries a doubled `T//` that `cd` collapses,
and `/var` is a symlink to `/private/var`, which `getcwd()` resolves. Measured here on
2026-08-09, the literal was `/var/folders/…/T//cmux-spawn-agent-smoke/smoke repo` and
the recorded cwd `/private/var/folders/…/T/cmux-spawn-agent-smoke/smoke repo` — `==`
answers False, `samefile` answers True. `cmux-spawn-agent` documents the doubled slash
as pure cosmetics and warns that "the trap is the fixing"; the trap here is the mirror
image, an equality assertion that turns the one check proving this plugin's
most-repeated bug is fixed into a guaranteed FAIL on every machine with a `$TMPDIR`.

## 8. The task, the address, and the reply

Send the task as a `SendMessage` to the address `python3 "$P" "<NAME>"` printed, with
the reply instruction spelled the way the measurements say it must be:

```
{"to": "uds:/tmp/cc-socks/<pid>.sock",
 "summary": "smoke worker - confirm the round trip",
 "message": "Reply with exactly one line and nothing else: SMOKE-OK <the name you were launched with>. Do not read files, run commands, or investigate anything.\n\nSend that reply to the session that sent you this message, using the `from` address on this message. (That session is named `<your own name>`.)"}
```

Three separate PASS conditions here:

- **`SendMessage` is accepted** against the `uds:` address. A bare name is refused on
  first contact and hands you a ref inline; if you find yourself holding a ref, you
  addressed it by name and that is a FAIL of the procedure, not of the plugin.
- **The reply arrives**, interrupting you, and its body contains `SMOKE-OK`. Match on
  the token, not on the whole body — workers inherit the user's global
  `~/.claude/CLAUDE.md`, so a reply may carry whatever preamble that file makes every
  session emit.
- **The reply was accepted on the worker's first attempt.** This is the reply-address
  contract, and it is the check with the sharpest measurement behind it — told to reply
  to the supervisor's *name*, 0 of 3 workers got through on the first try; told to use
  the `from` address on the message they received, 5 of 5 did. A refusal costs a round
  trip on the one message that carries the results, so it is silent unless you look:

```bash
cmux read-screen --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>" --scrollback --lines 200 \
  | grep -c "is not an agent in this conversation"
```

PASS on `0`. Any non-zero count means the worker addressed you by name, retried, and
paid for it — record it, because that is the regression, not a hiccup.

If the reply never arrives at all, check the watcher's lines before concluding
anything: `ATTN` means the message is being **held** for approval, which is what a
permission-class mismatch looks like from here (see check 1) and not a messaging
failure.

## 9. `DONE`, from the run's own watcher

PASS when the run's watcher prints `DONE <NAME>`.

**Expect it after the reply, not before.** The worker sends its reply as its last act
and delivery is immediate, while `DONE` waits on the next poll — up to a poll interval
later. `DONE` arriving *first*, or arriving with no reply at all, is still a PASS for
the watcher and a FAIL for check 8.

`GONE` here means the worker died. `CLEAR` means it stopped being blocked without
taking a turn, so nothing is coming and the task needs re-sending.

## 10. Optional — a second worker, if you want the pane-reuse path

Only if the run so far is clean and you have time. Spawn a second worker by the same
procedure and assert it lands as a **tab in the same pane**, not a second split:
`surface.py <new> pane_id` must equal the first worker's `pane_id`, and the workspace
pane count must not have moved. Skip it by default — check 7a already proves placement,
and this doubles the teardown.

## 11. Teardown, and proof that nothing leaked

Order matters, and it is the skill's order. Stopping the watcher last means one `GONE`
per surface you close, arriving exactly as you report a clean run.

1. **`TaskStop` both monitors** — the run's watcher and the deaf one from check 5.
2. **Close each surface in the ledger**, and only those:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/surface.py"
LEDGER="${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
while IFS=$'\t' read -r name su pu state; do
  [ -n "$su" ] || continue
  [ "$su" != "$CMUX_SURFACE_ID" ] || { echo "REFUSING to close the caller: $name"; continue; }
  ref=$(python3 "$S" "$su" ref)
  echo "$name  $su  ref=${ref:-already-closed}"
done < "$LEDGER"
```

   Then `cmux close-surface --workspace "$CMUX_WORKSPACE_ID" --surface "<ref>"` for
   each, one call at a time.

3. **Confirm each close by uuid, never by the echo.** `close-surface` prints back
   `OK surface:N` where N is an allocation counter unrelated to what you closed — it
   drifted on all three closes measured. The only trustworthy confirmation is:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/surface.py"
python3 "$S" "<su>" ref; echo "exit=$?  (want: no output, exit=1)"
```

4. **Delete the ledger file**, not just its rows, and the scratch fixtures:

```bash
rm -f "${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
rm -rf "${TMPDIR:-/tmp}/cmux-spawn-agent-smoke"
```

   The second line removes only this smoke test's own scratch directory — the throwaway
   repo, the ledger fixtures and the deaf watcher's ledger. It never touches the real
   ledger directory, which is the line above it.

5. **No watcher survived**, scoped to your own surface:

```bash
pgrep -fl "watch-workers.py.*${CMUX_SURFACE_ID}"
```

   PASS on no output. **Do not run this bare** — `pgrep -fl watch-workers.py` lists
   every watcher on the machine, including live ones belonging to other sessions, and
   an agent following that reported two healthy watchers as orphans, one of them its
   own supervisor's. If a line does come back it is yours and it is stuck:
   `pkill -f "watch-workers.py.*${CMUX_SURFACE_ID}"`.

6. **Topology is back to baseline** — re-run the counter from check 7 and compare with
   the numbers you recorded. Same pane count, same surface count. Anything left over is
   a leak, and the ledger you just deleted was the record of what it was, so say so
   explicitly rather than hunting for it later.

## The verdict

Report a table — check number, PASS / FAIL / SKIPPED / DENIED, and the literal signal.
Then one line of overall judgement, which is not a percentage:

- **PASS** — every check passed, or the only non-passes are SKIPPED with a stated
  reason. The plugin works on this machine.
- **PARTIAL** — checks 0 through 5 passed and something in 7 through 9 did not. The
  guards hold but the live path is broken. **This is not a pass**, and it is the
  failure mode worth naming loudly: every cheap check can pass while spawning is
  entirely dead, because none of them touches a worker.
- **FAIL** — anything in 0 through 5 failed, or teardown left a leak. Lead with the
  leak; a stranded worker or an orphaned watcher outlives the report.

Say which version you tested — check 0 printed it — and from which plugin root. A
result without those two is not reproducible, and under a `directory`-source install
the version string is not evidence of what was loaded anyway.
