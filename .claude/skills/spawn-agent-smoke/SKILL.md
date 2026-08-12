---
name: spawn-agent-smoke
description: Smoke-test the spawn-agent skill on this machine — a bounded, evidence-first run that proves a worker really spawns into a visible slot, becomes addressable, takes a task, replies, and is torn down without leaks. It detects whether this session is in cmux or in herdr and runs that host's variant of every host-specific check. Use when the user says "smoke test", "self-test", "does spawning still work", "verify the plugin", or runs /spawn-agent-smoke, and after editing the plugin or when a spawn run behaved oddly. Not for ordinary work — a request to spawn agents in order to get something done belongs to spawn-agent instead.
---

# Smoke-test `spawn-agent`

Eleven checks, one worker, about five minutes. It answers one question: **does this
plugin work on this machine right now** — after an edit, after a release, or when a
run has started feeling wrong.

**This skill is not shipped.** It lives in this repo's own `.claude/skills/`, so it
tests the working tree rather than anything a user installed. That is the point: in
this repo the working tree *is* what every session loads (see CLAUDE.md, "`git
checkout` is the deployment command"), and check 0 proves that rather than assuming
it.

It is not an audit. It does not read the skill for correctness; it makes the
machine prove the handful of things that have actually broken here, and that
reading cannot settle. Every check names the signal you look at and what its failure
means, because in this plugin several unrelated faults share one signature and "it
timed out" is not a diagnosis.

**Record as you go.** One row per check — number, PASS or FAIL, and the literal
output you based it on. Do not batch the verdict to the end; a run that dies at
check 8 still has seven results worth reporting.

**Three attempts, then move on.** The auto-mode classifier denies calls that would
have worked — a placement command was denied twice in a row and succeeded on the third
identical attempt. A denial never reaches the shell, so it looks exactly like a real
failure. Reissue the same `Bash` call up to three times before recording FAIL, and
record *denied* rather than *failed* when that is what it was.

**One command per `Bash` call.** Same reason as the skill under test — a long
compound command is denied as a unit and denials escalate. The blocks here are
grouped for reading.

## 0. Resolve the plugin root, then the staleness gate

Two things have to be true before anything below means anything: you must be testing
the bytes that sessions actually load, and this session must have been started after
those bytes were written.

### 0a. Which plugin root, and is it the one that gets loaded

This skill gets no `${CLAUDE_PLUGIN_ROOT}` — that token is substituted only for
skills inside a plugin, and this one deliberately is not. Resolve it from the repo,
then prove the resolution against every profile's marketplace registration:

```bash
python3 - <<'PY'
import glob, json, os, subprocess, sys

top = subprocess.run(["git","rev-parse","--show-toplevel"],
                     capture_output=True, text=True).stdout.strip()
if not top:
    sys.exit("FAIL not inside a git checkout -- cannot locate the plugin root")
root = os.path.join(top, "plugins", "agent-toolkit")
manifest = os.path.join(root, ".claude-plugin", "plugin.json")
if not os.path.exists(manifest):
    sys.exit("FAIL no plugin manifest at %s" % manifest)
print("repo         :", top)
print("plugin root  :", root)
print("version      :", json.load(open(manifest))["version"])

# Does any profile actually load from here?
profiles = [d for d in os.environ.get("CLAUDE_CONFIG_DIR","").split(":") if d] \
           or sorted(glob.glob(os.path.expanduser("~/.claude")) +
                     glob.glob(os.path.expanduser("~/.claude-*")))
agree = 0
for p in profiles:
    km = os.path.join(p, "plugins", "known_marketplaces.json")
    try:
        entry = json.load(open(km)).get("agent-toolkit")
    except (OSError, ValueError):
        continue
    if not entry:
        print("  %-24s not registered" % os.path.basename(p)); continue
    src = entry.get("source", {}).get("source")
    loc = entry.get("installLocation", "")
    same = bool(loc) and os.path.exists(loc) and os.path.samefile(loc, top)
    agree += same and src == "directory"
    print("  %-24s source=%-9s loads=%s%s"
          % (os.path.basename(p), src, loc, "  <-- THIS TREE" if same else ""))
print("ROOT:", "PASS - at least one profile loads this tree directly" if agree
      else "FAIL - no profile loads this tree; you would be testing bytes nobody runs")
PY
```

**A `github` source here is a FAIL, not a footnote.** Under a `directory` source
Claude Code loads the plugin from this working tree, so an edit is live in the next
session. Under a `github` source it serves a version-keyed cache instead, and this
whole procedure would be measuring a tree no session reads. Both profiles on this
machine were `directory` sources pointing at this repo when this was written.

**Keep the plugin root it prints.** Every block below writes it out in full; there is
no token to lean on.

### 0b. The staleness gate

**Skill text is resolved once, at session start, and cached for the life of the
process.** A session that started before the plugin changed is running the old text
and will smoke-test bytes nobody ships. Subagents inherit their parent's snapshot, so
re-running this inside a `Task` refreshes nothing.

**Only `SKILL.md` is snapshotted.** Everything else the skill ships — `lib/*.py`,
`hosts/*.md`, `hosts/*.py` — is opened at use time, by `Read` or by `python3`, so it is
always the current bytes on disk. Their mtimes are deliberately excluded below;
including them would fail sessions that are perfectly current, and the gate would then
be wrong in the *safe* direction, which is still wrong.

That split is worth holding onto when you are iterating: an edit to a **host file**
takes effect in the session you are already in, while an edit to **`SKILL.md`** needs a
new `claude`.

```bash
python3 - "<the plugin root check 0a printed>" $$ <<'PY'
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
print("newest text  :", time.ctime(newest), "->", os.path.relpath(newest_path, root))
print("session      :", pid, "started", time.ctime(started))
print("GATE:", "PASS - session postdates the skill text" if started > newest
      else "FAIL - STALE SESSION, start a fresh claude before smoke-testing")
PY
```

**It walks the process tree instead of taking the parent of `$$`, and that is not
tidiness.** Run through any extra shell layer, the naive form names that layer's
parent — a process born seconds ago, which is *newer* than any edit, so the gate
reports PASS for a session that is arbitrarily stale. Measured while writing this
file — through one wrapping `zsh` the naive form resolved a pid 2 h 38 m younger than
the real session and flipped a genuine FAIL to PASS, while the form above returned
the same `claude` pid at zero, one and two extra levels of shell. A gate that fails
open is worse than no gate.

**Keep the pid it prints.** Check 1 needs it.

**On FAIL, stop.** Open a new tab yourself and run `/spawn-agent-smoke` there — a
`claude` started after the edit is the only thing that loads it. Two notes on the
alternatives:

- Quitting and relaunching in **this** slot works too, and inherits this slot's
  ledger file, because the ledger is keyed by the host's slot id and that outlives
  any one `claude`. Check 2 is where that shows up.
- Having a *spawned worker* run this procedure also works — the worker is a new
  `claude` with a current snapshot. But a stale supervisor is following stale spawn
  instructions to create it, so report that the tab was opened under old text.

This gate is not theoretical. Writing the original on 2026-08-09, the authoring
session had started at 13:16:29 and the newest `SKILL.md` was written at 15:42:13 — a
session two and a half hours behind the text it would have claimed to test.

## 1. Preflight — which host, and five things that cost nothing

### 1a. Host detection, by precedence

```bash
if [ "${HERDR_ENV:-}" = 1 ]; then echo "HOST=herdr"
elif [ -n "${CMUX_SURFACE_ID:-}" ]; then echo "HOST=cmux"
else echo "HOST=none"; fi
```

PASS on `herdr` or `cmux`. **`none` means stop** — nothing below this line can run
outside a supported host. Record which host you got; every check marked *host* below
has one variant per host and you run only yours.

**Then prove the precedence is doing something, not just sitting there.** This is the
check that would have caught the bug the precedence rule exists for:

```bash
echo "HERDR_ENV=[${HERDR_ENV:-}]  HERDR_PANE_ID=[${HERDR_PANE_ID:-}]  CMUX_SURFACE_ID=[${CMUX_SURFACE_ID:-}]"
```

- On **herdr**, `CMUX_SURFACE_ID` being *non-empty* is not a failure — it is the
  expected inherited value when the herdr server was started from a cmux surface, and
  seeing it is a PASS for this check, because it proves the precedence rule is load
  bearing on this machine rather than theoretical. What would be a FAIL is the skill
  going on to *use* it, which checks 5 and 11 catch by asserting the ledger path.
- On **cmux**, `HERDR_ENV` must be empty. If both are set and `HERDR_ENV=1`, you are
  in herdr regardless of what the cmux variables say.

### 1b. Host preflight — *host*

**cmux:**

```bash
cmux --json identify
```

PASS if it returns JSON with a `caller` block. That block is where you are; `focused`
is wherever the user has drifted to, and the two routinely differ — they did while
this was written (`caller` in `workspace:3`, `focused` in `workspace:1`). Never place
anything by `focused`.

**herdr:**

```bash
herdr pane current --current
```

PASS if it returns a `PaneInfo` whose `pane_id` equals `$HERDR_PANE_ID`. Then run it
*without* `--current` and record the difference: without a target herdr answers with
the server's focused pane, which may belong to the user or another client. Two
different answers here is a PASS and is exactly why every later command passes an
explicit id.

Also assert the server is reachable, because every herdr check below depends on it:

```bash
herdr status 2>&1 | grep -A1 '^server' | grep -q 'running' && echo "PASS herdr server running" || echo "FAIL no herdr server"
```

### 1c. The name flag — *core*

```bash
claude --help | grep -- '-n, --name'
```

PASS on `-n, --name <name>  Set a display name for this session`. The name is the
only join key this plugin has; without the flag nothing downstream works, and the
failure would surface much later as a worker that never registers under the name you
expect.

### 1d. Permission class — *core*

```bash
CLPID="<the session pid check 0b printed>"
ps -o command= -p "$CLPID" \
  | grep -qE -- '--dangerously-skip-permissions|--permission-mode[= ]bypassPermissions' \
  && echo "FAIL bypass class - messaging will be held in both directions" \
  || echo "PASS prompting class"
```

Take the pid from check 0b rather than deriving it again — for the reason check 0b
gives, a second derivation is a second chance to inspect the wrong process, and here
that would clear a bypass session as safe.

A bypass-class supervisor is deaf and mute to peer messages by default — the task is
held at the worker and the reply is held here, each behind a dialog somebody has to
find. Checks 8 and 9 would then fail for a reason that has nothing to do with the
plugin. This grep only sees flags passed at launch; if the mode was changed in-session
the session's own status line is the authority (`⏵⏵ auto mode on`, and so on).

### 1e. The ledger rail — *host binding, core assertion*

```bash
# cmux:
CALLER_SLOT="$CMUX_SURFACE_ID"
# herdr:
CALLER_SLOT="${HERDR_PANE_ID//:/-}"
```

```bash
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
echo "ledger: $LEDGER"
[ -s "$LEDGER" ] && { echo "STOP this session already owns spawned workers:"; cat "$LEDGER"; } \
  || echo "PASS no live ledger for this slot"
```

**This is a rail, not a check.** A non-empty ledger means the session in this slot has
workers it has not finished with, and check 11 deletes the ledger and closes what it
lists. Run the smoke test somewhere else instead. Do not "just move it aside" — the
rows are the only record of which slots that run may close.

**On herdr, read the printed path before you continue.** It must contain the
sanitised pane id (`w9-p2`), not a colon and not a cmux uuid. A path with a uuid in it
is the inherited-`CMUX_SURFACE_ID` bug, live, and it is the one failure that would
silently merge this run's ledger with every other herdr pane's.

## 2. The stale-ledger guard — three fixtures, no spawn — *core*

The ledger path is keyed by the slot id, so relaunching `claude` in the same slot
reopens the previous run's file, rows and all, including rows in a shape this version
no longer writes. The watcher reads column 1 as a name; on a five-column ledger it
watches session uuids and matches nothing.

```bash
D="${TMPDIR:-/tmp}/spawn-agent-smoke"
mkdir -p "$D"
: > "$D/empty.tsv"
printf 'a\tb\tc\td\n' > "$D/ok4.tsv"
printf 'a\tb\tc\td\te\n' > "$D/legacy5.tsv"
```

```bash
D="${TMPDIR:-/tmp}/spawn-agent-smoke"
for f in empty ok4 legacy5; do
  awk -F'\t' 'NF && NF!=4 {print FILENAME": "NR" columns="NF; bad=1} END{exit bad}' "$D/$f.tsv"
  echo "$f -> exit=$?"
done
```

PASS on exactly `empty -> exit=0`, `ok4 -> exit=0`, and `legacy5 -> exit=1` preceded
by a `columns=5` line naming the file. Measured in that form on 2026-08-09.

**Fixtures, never the real ledger.** An empty file must pass — the setup block
`touch`es one before the first row exists — and a five-column file must be refused
loudly, because the alternative is a watcher that runs happily and reports nothing for
the whole run.

## 3. The name-collision guard — it must ask for `name`, not for the address — *core*

A name collision mis-delivers a task: `peer.py` hands back the wrong socket and the
watcher reports the wrong worker. The guard is only worth anything if it sees names
that are in use but unreachable, which on this machine is most of them.

```bash
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
P="<plugin root>/skills/spawn-agent/lib/peer.py"
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

**Expect that skip to become permanent, and say so when it does.** The
named-but-unreachable session is a pre-v2.1.224 artifact, so it disappears from a
machine as its sessions turn over — measured 2026-08-12, twice in one day, `10 live
sessions, 10 with a socket, 0 without`, where three days earlier the same machine had
6 of 7 without. Once a machine is fully updated there is nothing left for the
name-versus-address contrast to bite on, and this check reports SKIPPED forever while
the guard it covers goes untested. That is a gap to close with a synthetic registry
fixture, not a result to keep re-recording; note it in the verdict rather than letting
a permanent SKIPPED read as a temporary one.

## 4. The prune, in both directions — *core*

Both halves of the documented one-liner are load-bearing and they fail in **opposite**
directions, so a run that only ever exercises one of them proves nothing.

```bash
D="${TMPDIR:-/tmp}/spawn-agent-smoke"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\n' > "$LEDGER"
l1=SU-AAA
[ -n "$l1" ] && { grep -v -F "$l1" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "A: rows=$(wc -l < "$LEDGER" | tr -d ' ') tmp=$(ls "$LEDGER.tmp" 2>/dev/null | wc -l | tr -d ' ')"
```

PASS on `A: rows=0 tmp=0`. This is the last-row case, which on a one-worker run is the
only prune the run ever does — joined with `&&` instead of `;` the `mv` is skipped
(because `grep` exits 1 when it selects nothing), the ghost row survives, and a `.tmp`
is left behind. `tmp=1` is that bug.

```bash
D="${TMPDIR:-/tmp}/spawn-agent-smoke"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\nw2\tSU-BBB\tPU-BBB\treported\n' > "$LEDGER"
unset l1
[ -n "$l1" ] && { grep -v -F "$l1" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "B: rows=$(wc -l < "$LEDGER" | tr -d ' ') bytes=$(wc -c < "$LEDGER" | tr -d ' ')"
```

PASS on `B: rows=2 bytes=52` — untouched. An unset locator is reachable by *following*
the skill rather than by ignoring it, since variables die with each `Bash` call and the
prune reads as a standalone command. Without the `[ -n "$l1" ]` guard, `grep -v -F ""`
selects nothing and installs it over the whole file: `rows=0 bytes=0`, **exit 0**. The
destruction reports success, and what it destroys is the record of which slots the run
is allowed to close. Both directions measured 2026-08-09.

## 5. Arm a deaf watcher and wait for `WARN` — *core*

A watcher that matches nothing used to be indistinguishable from a healthy watcher
whose workers are still busy. Prove it now says so. This runs **concurrently** with
everything below — arm it, keep going, and collect its line later.

Name the scratch ledger with this slot's id so the leak proof in check 11 catches
this watcher too.

Create its ledger first, with one row naming nobody:

```bash
D="${TMPDIR:-/tmp}/spawn-agent-smoke"
mkdir -p "$D"
printf 'smoke-warn-nobody\tL1-X\tL2-X\tspawned\n' > "$D/warn-${CALLER_SLOT}.tsv"
```

Then arm the `Monitor` tool with this `command`, **with `<plugin root>` and
`<CALLER_SLOT>` written out literally** — `Monitor` runs its own shell, which never saw
your assignments:

```bash
python3 -u "<plugin root>/skills/spawn-agent/lib/watch-workers.py" \
    "${TMPDIR:-/tmp}/spawn-agent-smoke/warn-<CALLER_SLOT>.tsv" 1
```

PASS on exactly one line, about 30 seconds in:

```
WARN ledger has 1 row(s), none match a live session name: /…/warn-<slot>.tsv
```

Measured in that form on 2026-08-09. Note the shape of the assertion: **one** line,
naming the "rows match nothing" case, and no second one afterwards. Two `WARN`s, or a
`GONE`, or silence past a minute, are all FAIL. Silence in particular is the exact
regression this exists to catch.

Keep the task id. Check 11 stops it.

## 6. Rails for everything below this line

The remaining checks create and destroy a real slot. These are not advice:

- Spawn only into **your own workspace**, anchored on your own slot. Never into what
  is focused.
- **Close nothing that is not in this run's ledger**, however idle it looks.
- **Never close your own slot** (`$CMUX_SURFACE_ID` / `$HERDR_PANE_ID`). Assert it by
  hand before every close.
- **cmux:** never `close-others`, `close-left`, `close-right`, or `close-workspace`.
  One split for the whole run.
- **herdr:** never `herdr workspace close`, and **never `herdr server stop`** — it
  kills every pane process the server owns, including the user's. One tab per worker.

## 7. One real spawn — follow `spawn-agent`, do not reimplement it

**Open `<plugin root>/skills/spawn-agent/SKILL.md` and the host file for your host,
and follow "Spawn one agent" as written.** Read them from disk with `Read` — this
skill is not the plugin, so it has no loaded copy to lean on. The point of the check
is that the shipped procedure works; a hand-rolled launch tests your typing. Two deliberate substitutions, and no others:

- `NAME` is `smoke-$$` or anything else unlikely to collide, within
  `[a-z][a-z0-9_-]{0,31}`.
- `REPO` is a **throwaway checkout whose path contains a space and is unique to this
  run**, created below. Both properties are load-bearing. The space exercises cmux's
  quoting fix — an unquoted path dies at `cd` before `claude` ever starts, and zsh's
  message for it is the unhelpful `string not in pwd` — and on both hosts it gives the
  cwd verification power it does not otherwise have, since a comparison against a path
  you would have landed in anyway proves nothing.

```bash
CLPID="<the session pid check 0b printed>"
REPO="${TMPDIR:-/tmp}/spawn-agent-smoke/smoke repo $CLPID"
mkdir -p "$REPO"
git -C "$REPO" init -q
[ -d "$REPO/.git" ] && echo "PASS scratch repo: $REPO"
```

**The pid suffix is what keeps the folder-trust gate reachable, and without it this
procedure quietly stops testing the thing it claims to.** Trust is recorded per path in
the profile's `~/.claude.json` as `hasTrustDialogAccepted`, and it **outlives the
directory** — teardown's `rm -rf` deletes the folder while the trust record stays. So a
fixed scratch path is gated on a machine's *first* run and silently pre-trusted on every
run after that, which retires the gate-clearing half of 7d and 7e and all of 7d's
failure-signature table. Caught on 2026-08-12, when a cmux run registered at `n=0` with
no gate at all and the earlier record was still in the profile — including one under the
pre-rename `…/cmux-spawn-agent-smoke/smoke repo`.

A fresh pid per run means a fresh path, so the gate fires every time. It also means the
profile accumulates one trust record per smoke run; they are inert, and clearing them is
optional housekeeping, not part of this procedure.

Record the baseline before you place anything — *host*:

```bash
# cmux: panes and surfaces in this workspace
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

```bash
# herdr: tabs and panes in this workspace
herdr tab list --workspace "$HERDR_WORKSPACE_ID" | python3 -c '
import json, sys
t = json.load(sys.stdin)["result"]["tabs"]
print(len(t), "tabs,", sum(x["pane_count"] for x in t), "panes")'
```

Then follow the skill: collision check, place the slot, resolve both locators,
**write the ledger row**, arm the run's watcher, launch, wait for addressable.

### 7a. The slot is new, and it is not yours — *host*

The invariant is the same on both hosts and it is the one that matters: **the worker
did not land on top of the session the user is talking to.** The assertion differs
because the nesting is inverted.

**cmux** — the worker must be in a different *pane* from the caller, since a new
surface becomes its pane's selected tab and would cover you:

```bash
S="<plugin root>/skills/spawn-agent/hosts/cmux-surface.py"
SURF="<the surface:N the split printed>"
echo "worker pane: $(python3 "$S" "$SURF" pane_id)"
echo "caller pane: $(python3 "$S" "$CMUX_SURFACE_ID" pane_id)"
```

PASS when both resolve and the two uuids **differ**, and the topology count has gone
up by one pane and one surface.

**herdr** — the worker must be in a different *tab*, and the caller's tab must not
have gained a pane:

```bash
echo "worker pane: $L1"
herdr pane get "$L1" | python3 -c 'import json,sys; print("worker tab:", json.load(sys.stdin)["result"]["tab_id"])'
echo "caller pane: $HERDR_PANE_ID  caller tab: $HERDR_TAB_ID"
```

PASS when the worker's `tab_id` differs from `$HERDR_TAB_ID`, and the baseline count
has gone up by exactly one tab and one pane. A worker in your own tab is a split that
shrank the user's view, which is the herdr version of the same mistake.

### 7b. The row is on disk before anything is launched — *core*

```bash
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
awk -F'\t' '{printf "%d: %d cols:", NR, NF; for (i=1; i<=NF; i++) printf " [%s]", $i; print ""}' "$LEDGER"
```

**Brackets, and no whole-record reference.** The brackets are what make an empty field
visible — this check's own FAIL criterion is "two blank-looking fields", which
unbracketed output cannot show you. And awk's whole-record variable, a dollar sign
followed by the digit zero, cannot appear here at all: a skill invoked with arguments
has every bare dollar-plus-digit replaced by one of those arguments before you read it.
Measured 2026-08-12 — an earlier version of this very block was served as
`{print NR": "NF" cols: "Run}`, `Run` being the first word of that run's arguments and
an uninitialised awk variable, so it printed every row blank on a perfectly healthy
ledger and framed it as the failure it was written to detect. `$i` is safe; only
dollar-plus-digit is substituted.

```bash
P="<plugin root>/skills/spawn-agent/lib/peer.py"
python3 "$P" "<NAME>" name; echo "registry exit=$?"
```

PASS when the ledger holds exactly one four-column row naming your worker **and** the
registry lookup exits 1. That pair is the whole ordering guarantee: a recorded slot
that is not yet running anything. Reversed, the window between the two holds a live
agent no ledger knows about, and a turn that dies inside it orphans the slot forever.

`awk: can't open file …` means no row was written at all — the launch happened without
a ledger, or the path is not the one the setup block built. Either is a FAIL here.

Two blank-looking fields in that row is a FAIL, not cosmetics — tab is IFS whitespace,
so the cleanup loop's `read` shifts every column left and the row can never be offered
for closing.

Check what columns 2 and 3 hold for your host: cmux writes a surface uuid and a pane
uuid; herdr writes a pane id (`w9:p3`) and a terminal id (`term_…`).

### 7c. Read the launch before you send it — *host*

**cmux:** the `cd` is the single most-dropped part of the launch line, it sits
mid-string, and a supervisor that had just read the warning dropped it on the very next
line it wrote. Before sending, confirm your `cmux send` payload literally contains
`cd \"$REPO\" && claude -n <NAME>` — escaped double quotes, not single ones, not bare.
Record the string you sent.

**herdr:** there is no shell payload to inspect, and the quoting class of bug cannot
occur, because `--cwd` is an argv element. Two different things must be true instead:

- `--cwd "$REPO"` was passed to `tab create` (or `pane split`). Omitted, the pane lands
  in the workspace default — measured as `/Users/ns`, **not** the caller's cwd — and
  then gates on *that* directory.
- the claude flags rode **after** the `--` separator in
  `herdr agent start <name> --kind claude --pane <id> -- -n <NAME>`. Without it herdr
  exits **2** with `unknown option: -n` and starts nothing. Record the exit code you
  got; a 2 here means you dropped the separator.

### 7d. Readiness, and the failures that look identical — *core, with a host twist*

Run the skill's readiness loop, but bound it at 20 rather than 60 — you are expecting
a gate here, not hoping to avoid one:

```bash
P="<plugin root>/skills/spawn-agent/lib/peer.py"
n=0
until python3 "$P" "<NAME>" >/dev/null; do
  sleep 1
  n=$((n+1))
  [ "$n" -gt 20 ] && { echo "not addressable after ${n}s"; break; }
done
echo "loop ended at n=$n"
```

A worker that never registers costs 21.8 s of wall clock there, measured — so the
bound is the difference between a check and a stall.

**On herdr this check has teeth that it does not have on cmux, and this is the single
most important host difference in the file.** `herdr agent start` returns
`interactive_ready: true`, `agent_status: "idle"`, exit 0, in about three seconds —
*while the worker is parked on the trust gate and has not registered at all*
(measured twice, 2026-08-12). So on herdr:

```
PASS = the loop hit its bound (or peer.py exits 1) even though `agent start` said ready
```

That is not a failure of the plugin; it is the plugin's readiness loop catching a host
that reports readiness it does not have. Record `agent start`'s own claim next to
`peer.py`'s answer — the contradiction *is* the evidence. Cross-check with the title:

```bash
herdr agent get "<NAME>" | python3 -c 'import json,sys; a=json.load(sys.stdin)["result"]; a=a.get("agent",a); print("title:",a["terminal_title"])'
```

`claude -n <NAME> …` (the raw argv) means gated; `✳ <NAME>` means really running.

If the loop exits early on cmux, PASS, and skip to 7e's trust-gate paragraph only if a
gate is on screen. If it hits the bound, **read the screen — this is mandatory and it
is the whole point of the check:**

```bash
cmux read-screen --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>"
herdr pane read "$L1" --source visible --lines 40
```

No worker signal fires during this window. `peer.py` exits 1; `ASK`, `ATTN` and `GONE`
are all polled from a registry record that does not exist yet. The screen is the only
place the reason is written:

| What the screen shows | What it means | What to do |
| --- | --- | --- |
| a shell prompt, `zsh:cd:1: string not in pwd: …` above it (cmux only) | the launch line lost its quoting and died at `cd` | FAIL check 7c. The payload was wrong, not the plugin |
| `Quick safety check: Is this a project you created or one you trust?` | the folder-trust gate — expected here, since the scratch repo is new to this profile | clear it, below, and count it as PASS |
| a shell prompt with no error, in the wrong directory | `--cwd` / `cd` was dropped | FAIL check 7c |
| a Claude prompt, no dialog | it booted but has not registered yet | re-run the loop once with a 40 bound before recording FAIL |

### 7e. Clear the gate — and prove the directory it names — *core*

**Read the directory in the dialog before you press anything.** It must be the scratch
repo. The gate is answered by `enter` **alone**, because option 1 is already selected:

```bash
cmux send-key --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>" enter
herdr pane send-keys "$L1" enter
```

**Do not send `down` first.** On the plain variant of that dialog option 2 is
"No, exit", so the reflexive rescue sequence terminates the worker it was meant to
save. Count the `❯` row in the output you just read; if it is already on option 1,
`enter` is the entire answer.

On herdr, also record the negative control if you have time: `esc` on that gate
cancels and exits `claude` cleanly without trusting anything (measured 2026-08-12).
That is the correct answer when the directory named is not the one you intended, and
knowing it is what keeps a supervisor from trusting a stranger's folder to unstick a
worker.

Then re-run the readiness loop, and verify where it landed. Compare the two as
**paths, not as strings**:

```bash
P="<plugin root>/skills/spawn-agent/lib/peer.py"
CLPID="<the session pid check 0b printed>"
REPO="${TMPDIR:-/tmp}/spawn-agent-smoke/smoke repo $CLPID"
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

**Do not assert the two strings are equal.** On macOS a perfectly healthy run prints
two visibly different paths, for two independent reasons that both apply at once:
`$TMPDIR` ends in a slash, so the literal carries a doubled `T//` that gets collapsed,
and `/var` is a symlink to `/private/var`, which `getcwd()` resolves. Measured here on
2026-08-09, the literal was `/var/folders/…/T//spawn-agent-smoke/smoke repo` and the
recorded cwd `/private/var/folders/…/T/spawn-agent-smoke/smoke repo` — `==` answers
False, `samefile` answers True. `spawn-agent` documents the doubled slash as pure
cosmetics and warns that "the trap is the fixing"; the trap here is the mirror image,
an equality assertion that turns the one check proving this plugin's most-repeated bug
is fixed into a guaranteed FAIL on every machine with a `$TMPDIR`.

## 8. The task, the address, and the reply — *core*

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
  the `from` address on the message they received, 5 of 5 did, and again 1 of 1 under
  herdr on 2026-08-12. A refusal costs a round trip on the one message that carries the
  results, so it is silent unless you look — *host*:

```bash
cmux read-screen --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>" --scrollback --lines 200 \
  | grep -c "is not an agent in this conversation"
```

```bash
# herdr: only once the worker is idle -- see below
herdr agent read "<NAME>" --source recent-unwrapped --lines 200 \
  | grep -c "is not an agent in this conversation"
```

PASS on `0`. Any non-zero count means the worker addressed you by name, retried, and
paid for it — record it, because that is the regression, not a hiccup.

**On herdr this read has two failure modes and only one of them is loud.**

- **Too early is loud.** `recent`/`recent-unwrapped` refuse outright while the agent is
  working — `{"error":{"code":"agent_not_idle",…"its alternate-screen history can only
  be captured by scrolling while idle"}}` — because Claude Code runs on the alternate
  screen and herdr has to scroll it to capture history. Wait for idle and retry; that
  error is not a FAIL of anything.
- **The wrong source is silent.** Substituting `--source visible` because it works
  while busy returns a clean `0` having never looked at the line in question. Measured
  against a worker that had emitted 120 marked lines, `visible` returned 46 matches at
  `--lines 200` and 46 again at `--lines 600` — it saturates at the viewport and says
  nothing about it, while `recent-unwrapped` returned all of them.

So the order matters: wait for idle, *then* read with `recent-unwrapped`. Recording a
`0` obtained from `visible` is recording nothing.

If the reply never arrives at all, check the watcher's lines before concluding
anything: `ATTN` means the message is being **held** for approval, which is what a
permission-class mismatch looks like from here (see check 1d) and not a messaging
failure.

## 9. `DONE`, from the run's own watcher — *core*

PASS when the run's watcher prints `DONE <NAME>`.

**Expect it after the reply, not before.** The worker sends its reply as its last act
and delivery is immediate, while `DONE` waits on the next poll — up to a poll interval
later. `DONE` arriving *first*, or arriving with no reply at all, is still a PASS for
the watcher and a FAIL for check 8.

`GONE` here means the worker died. `CLEAR` means it stopped being blocked without
taking a turn, so nothing is coming and the task needs re-sending.

**On herdr, do not accept the host's own status in place of this.** herdr publishes
`idle` / `working` / `blocked` / `done` / `unknown` per pane, and those states agreed
exactly with the registry when measured — but they are a screen classifier, and
`unknown` explicitly does not prove completion or death. The `DONE` this check wants is
the watcher's, from the pid-checked registry. Recording herdr's state alongside it is
useful evidence; substituting it is not a pass.

## 10. Optional — a second worker — *host*

Only if the run so far is clean and you have time. Skip it by default; check 7a already
proves placement, and this doubles the teardown.

**cmux:** spawn a second worker and assert it lands as a **tab in the same pane** —
`cmux-surface.py <new> pane_id` must equal the first worker's `pane_id`, and the
workspace pane count must not have moved. That is the "one split per run" guarantee.

**herdr:** spawn a second worker and assert it lands in a **second tab** — its
`tab_id` differs from the first worker's and from `$HERDR_TAB_ID`, the tab count rose
by exactly one, and no existing tab's `pane_count` changed. That is the "a fan-out
costs the user nothing" guarantee, and it is the opposite assertion to cmux's on
purpose.

## 11. Teardown, and proof that nothing leaked

Order matters, and it is the skill's order. Stopping the watcher last means one `GONE`
per slot you close, arriving exactly as you report a clean run.

1. **`TaskStop` both monitors** — the run's watcher and the deaf one from check 5.
2. **Close each slot in the ledger**, and only those:

```bash
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
while IFS=$'\t' read -r name l1 l2 state; do
  [ -n "$l1" ] || continue
  case "$l1" in "$CMUX_SURFACE_ID"|"$HERDR_PANE_ID") echo "REFUSING to close the caller: $name"; continue;; esac
  echo "$name  l1=$l1  l2=$l2  state=$state"
done < "$LEDGER"
```

   Then close one at a time — `cmux close-surface --workspace "$CMUX_WORKSPACE_ID"
   --surface "<ref>"`, or `herdr tab close "<tab>"` for the tab you created.

3. **Confirm each close by re-resolving, never by the echo.**

```bash
python3 "<plugin root>/skills/spawn-agent/hosts/cmux-surface.py" "<l1>" ref; echo "exit=$?  (want: no output, exit=1)"
herdr pane get "<l1>"; echo "exit=$?  (want: pane_not_found, exit=1)"
```

   On cmux this matters more than it looks: `close-surface` prints back `OK surface:N`
   where N is an allocation counter unrelated to what you closed — it drifted on all
   three closes measured. On herdr `tab close` returns a bare `{"type":"ok"}` with no
   id to misread, which is a smaller trap but the same discipline.

4. **Delete the ledger file**, not just its rows, and the scratch fixtures:

```bash
rm -f "${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
rm -rf "${TMPDIR:-/tmp}/spawn-agent-smoke"
```

   The second line removes only this smoke test's own scratch directory — the throwaway
   repo, the ledger fixtures and the deaf watcher's ledger. It never touches the real
   ledger directory, which is the line above it.

5. **No watcher survived**, scoped to your own slot:

```bash
pgrep -fl "watch-workers.py.*${CALLER_SLOT}"
```

   PASS on no output. **Do not run this bare** — `pgrep -fl watch-workers.py` lists
   every watcher on the machine, including live ones belonging to other sessions, and
   an agent following that reported two healthy watchers as orphans, one of them its
   own supervisor's. If a line does come back it is yours and it is stuck:
   `pkill -f "watch-workers.py.*${CALLER_SLOT}"`.

6. **Topology is back to baseline** — re-run the counter from check 7 and compare with
   the numbers you recorded. Same counts. Anything left over is a leak, and the ledger
   you just deleted was the record of what it was, so say so explicitly rather than
   hunting for it later.

## The verdict

Report a table — check number, PASS / FAIL / SKIPPED / DENIED, and the literal signal.
Then one line of overall judgement, which is not a percentage:

- **PASS** — every check passed, or the only non-passes are SKIPPED with a stated
  reason. The plugin works on this machine, on this host.
- **PARTIAL** — checks 0 through 5 passed and something in 7 through 9 did not. The
  guards hold but the live path is broken. **This is not a pass**, and it is the
  failure mode worth naming loudly: every cheap check can pass while spawning is
  entirely dead, because none of them touches a worker.
- **FAIL** — anything in 0 through 5 failed, or teardown left a leak. Lead with the
  leak; a stranded worker or an orphaned watcher outlives the report.

State three things or the result is not reproducible: **which host** you ran under,
**which plugin root** check 0a resolved and whether a profile actually loads it, and
**the git revision** of this working tree (`git rev-parse --short HEAD`, plus whether
it was dirty). The version string in `plugin.json` is *not* evidence of what was
loaded under a `directory` source — the tree is.

**One host is half the answer.** A clean run proves this plugin works under the host
you were in. The other host's variants are untested until someone runs this there, so
say which one you did not cover.
