---
name: spawn-agent-smoke
description: Smoke-test the spawn-agent skill on this machine — a bounded, evidence-first run that proves a worker really spawns into a visible slot, becomes addressable, takes a task, replies, and is torn down without leaks. It detects whether this session is in cmux or in herdr and runs that host's variant of every host-specific check. Use when the user says "smoke test", "self-test", "does spawning still work", "verify the plugin", or runs /spawn-agent-smoke, and after editing the plugin or when a spawn run behaved oddly. Not for ordinary work — a request to spawn agents in order to get something done belongs to spawn-agent instead.
---

# Smoke-test `spawn-agent`

Twelve checks, two workers, about eight minutes. It answers one question: **does this
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
identical attempt. The denial says so — it comes back as an explicit, labelled error
block naming the classifier, so you can tell it from a real failure by reading it.
What it does not tell you is anything about the command: a denial is no evidence the
command would have failed, and the classifier is not deterministic across identical
attempts. Reissue the same `Bash` call up to three times before recording FAIL, and
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

**The same answer both ways is not a failure either** — it means focus simply happened
to be on the calling pane when you looked, which is common when the user is watching
the session that is running this. Record it and move on. The assertion that decides
this check is `pane_id == $HERDR_PANE_ID` under `--current`; the divergence is evidence
when it appears, not a requirement. Observed identical on 2026-08-12.

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

Your host's binding line — one of these, not both:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr
```

**Re-derive it in every later block that uses it, and never carry it forward.** A
`Bash` call's shell state does not outlive the call — measured 2026-08-12, `export
CALLER_SLOT_PROBE=…` read back **empty** in the next `Bash` call *and* empty inside a
`Monitor` command, while `$CMUX_SURFACE_ID` was visible in both because it is a real
environment variable. So a block below that merely *uses* `${CALLER_SLOT}` is using the
empty string, and none of the paths it builds will error — they will quietly be wrong.
The blocks below therefore each carry the binding and a guard. That is not repetition
to be tidied away; deleting it is the bug.

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or the herdr line above
[ -n "$CALLER_SLOT" ] || { echo "FAIL empty slot -- stop here"; exit 1; }
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
echo "ledger: $LEDGER"
[ -s "$LEDGER" ] && { echo "STOP this session already owns spawned workers:"; cat "$LEDGER"; } \
  || echo "PASS no live ledger for this slot"
```

**Read the printed path on both hosts.** `…/spawn-agent/.tsv` — nothing between the
slash and the `.tsv` — is the empty-slot failure above, and it is the one to catch here
because every later check builds the same path. On cmux it must carry the surface uuid.

**This is a rail, not a check.** A non-empty ledger means the session in this slot has
workers it has not finished with, and check 12 deletes the ledger and closes what it
lists. Run the smoke test somewhere else instead. Do not "just move it aside" — the
rows are the only record of which slots that run may close.

**On herdr, read the printed path before you continue.** It must contain the
sanitised pane id (`w9-p2`), not a colon and not a cmux uuid. A path with a uuid in it
is the inherited-`CMUX_SURFACE_ID` bug, live, and it is the one failure that would
silently merge this run's ledger with every other herdr pane's.

**What that forbids is a colon or a cmux uuid — not a letter.** herdr slot ids are opaque
and routinely letter-suffixed, and one of those can read as a placeholder that never got
substituted: the examples in this file are all `w9:p2`-shaped, so a caller pane of `w9:pN`
with a ledger at `w9-pN.tsv` looks exactly like a literal `<N>` left behind by a broken
expansion. It is not one. `hosts/herdr.md` says so outright — "Ids are opaque and you must
not pattern-match them" — and on 2026-08-12 `herdr pane list` confirmed `w9:pN` as a real,
live pane while a careful cmux agent, reading that run's evidence, was flagging the
filename as suspect. The suspicion was reasonable and the conclusion was wrong; settle it
with `herdr pane list`, never with the shape of the string.

## 2. The stale-ledger guard — three fixtures, no spawn — *core*

The ledger path is keyed by the slot id, so relaunching `claude` in the same slot
reopens the previous run's file, rows and all, including rows in a shape this version
no longer writes. The watcher reads column 1 as a name; on a five-column ledger it
watches session uuids and matches nothing.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
mkdir -p "$D"
: > "$D/empty.tsv"
printf 'a\tb\tc\td\te\tf\tcmux\n' > "$D/ok7.tsv"
printf 'a\tb\tc\td\te\tf\t\n' > "$D/nohost7.tsv"
printf 'a\tb\tc\td\te\tf\n' > "$D/legacy6.tsv"
printf 'a\tb\tc\td\n' > "$D/legacy4.tsv"
```

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
for f in empty ok7 nohost7 legacy6 legacy4; do
  awk -F'\t' -v k=7 'NF && (NF!=7 || $k=="") {print FILENAME": "NR" columns="NF" host=["$k"]"; bad=1} END{exit bad}' "$D/$f.tsv"
  echo "$f -> exit=$?"
done
```

PASS on exactly `empty -> exit=0`, `ok7 -> exit=0`, `nohost7 -> exit=1`,
`legacy6 -> exit=1` and `legacy4 -> exit=1`, each refusal preceded by its own line
naming the file — `columns=7 host=[]`, `columns=6 host=[]`, `columns=4 host=[]`.
Measured in that order 2026-08-17.

**Two refused fixtures are formats this skill itself shipped, and that inversion is the
point.** Four columns was the format until ownership landed; six was the format until
the host column landed. Each has to be rejected for its own reason. A four-column row
carries no minted session id, so it cannot prove the worker is ours, and `owned.py`
exits 3 on exactly that. A six-column row proves ownership fine but names no host, so
its columns 2 and 3 cannot be safely resolved by anybody — which is the defect the
seventh column exists for. A `columns=6` line here is not a bug in the row you just
wrote; it is another session's ledger, inherited through the slot.

**`nohost7` is the fixture for the term that is easy to mistake for padding.** `awk`
counts a trailing empty field, so a row ending in a tab is seven columns wide and says
nothing about its host — `NF!=7` passes it and only the emptiness test catches it.

**And there is one thing this check provably cannot catch**, which is why the skill
writes `-` and not `""` into column 6: a row whose *sixth* field is empty still counts
seven fields with a non-empty tag, so it exits 0 here while the cleanup loop reads it
back as `pid=[cmux] host=[]`. Measured 2026-08-17. The placeholder is the only guard
against that; this check is not a second one.

**Fixtures, never the real ledger.** An empty file must pass — the setup block
`touch`es one before the first row exists.

## 3. The collision guard, and the address `peer.py` must refuse to mint — *core*

A name collision mis-delivers a task, and the deeper failure is that a name resolved
to a socket at all. `peer.py` now answers one question — *is this name taken* — and
**refuses to hand out an address for any of them**, because a name it resolved could
belong to any session holding that name, including one the user started by hand.

The guard is still only worth anything if it sees a name that is **in use but
unreachable** — registered under a live pid, with no messaging socket. Build that
session rather than hunting the machine for one:

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/fixture-profile"
mkdir -p "$D/sessions"
printf '{"pid":1,"name":"smoke-victim-probe","cwd":"/","sessionId":"fixture-only"}\n' > "$D/sessions/1.json"
```

```bash
P="<plugin root>/skills/spawn-agent/lib/peer.py"
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/fixture-profile"
CLAUDE_CONFIG_DIR="$D" python3 "$P" smoke-victim-probe name;    echo "  name    exit=$?"
CLAUDE_CONFIG_DIR="$D" python3 "$P" smoke-victim-probe address; echo "  address exit=$?"
CLAUDE_CONFIG_DIR="$D" python3 "$P" no-such-session-xyz name;   echo "  absent  exit=$?"
```

PASS on exactly:

```
smoke-victim-probe
  name    exit=0
  address exit=1
  absent  exit=1
```

with the `address` line preceded by a refusal on stderr naming `owned.py`. **That
refusal is the whole check.** It must fail for the *categorical* reason — this script
does not serve addresses — and not merely because this fixture happens to lack a
socket. Reversed, one `peer.py <name>` turns any name off `ListAgents` into a live
`uds:` target, which is the reported incident in a single command.

One property of `peer.py` makes that fixture work, and it is load-bearing:
- **`alive()` returns `True` on `PermissionError` from `os.kill(pid, 0)`**, and a
  non-root user signalling **pid 1** (launchd on macOS) raises exactly that. So pid 1
  is permanently "alive" with no race against a real process and no cleanup dependency.

**`CLAUDE_CONFIG_DIR` no longer makes this hermetic, and you should not expect it to.**
`roots()` puts that variable first and then appends `~/.claude` and every `~/.claude-*`,
because the name namespace is machine-wide while discovery is not — measured 2026-08-13,
15 live sessions across two profiles of which the default profile could see 9, so a
single-profile check calls a name free that another profile is holding. The fixture
still works because nothing real is called `smoke-victim-probe`; it is isolated by its
name, not by its profile.

**A sentinel pid will not do here, and it fails in the direction you would not guess.**
`alive()` rejects non-positive pids *before* it signals anything — `pid <= 0` returns
`False` — so a record carrying `"pid":-1` or `"pid":0` is simply dead to `peer.py`, the
`name` form exits 1, and the fixture fails its own PASS block on the very first line.
Measured 2026-08-12 against this `peer.py`: `pid=1` prints the name at exit 0, while
`pid=-1` and `pid=0` each print nothing at exit 1. That guard exists because a *naive*
liveness test — `os.kill(rec.get("pid") or -1, 0)` — signals `-1`, which addresses every
process the user may signal and answers *yes*, classing a record with no usable pid as
alive; `peer.py` carries a comment about it at `alive()`. The fixture therefore needs a
pid that the guard accepts and the kernel will answer for, which is why it uses a real
one.

Cleanup is already covered — the fixture sits under `${TMPDIR:-/tmp}/spawn-agent-smoke/`,
which check 12 step 4 removes along with the rest of this run's scratch. There is no
extra teardown step, and because that directory is keyed by `$CLPID` it is this run's
alone — a concurrent smoke run on the same machine has its own.

**Why a fixture and not the live registry.** Until 2026-08-12 this check hunted the
machine's own sessions for a named-but-socketless one. That shape is a pre-v2.1.224
artifact, so it ages off a machine as sessions turn over: 6 of 7 live sessions had no
socket on 2026-08-09, and 0 of 10 three days later. The hunt started coming back empty,
the check recorded SKIPPED, and on a fully-updated machine that skip was permanent — a
reassuring SKIPPED reported forever while the guard underneath it went untested. The
fixture makes this a real PASS/FAIL on every run, on every host, updated or not.

The census that revealed it is still worth running once, for context. Record it in the
run's notes **beside** check 3's row — never as check 3's own "literal signal", which is
the four-line PASS block above and nothing else, and never as a gate on whether the
check runs:

```bash
python3 -c '
import glob, json, os
roots = [d for d in os.environ.get("CLAUDE_CONFIG_DIR", "").split(":") if d] or [os.path.expanduser("~/.claude")]
live = sock = 0
for d in roots:
    for p in glob.glob(os.path.join(d, "sessions", "*.json")):
        try:
            r = json.load(open(p))
        except (OSError, ValueError):
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
        live += 1
        sock += bool(r.get("messagingSocketPath"))
print("%d live sessions, %d with a socket, %d without" % (live, sock, live - sock))'
```

## 3b. Ownership — `owned.py` must refuse whatever it cannot prove — *core*

Checks 2 and 3 prove a name can be seen. This one proves a name is **not enough**, and
that a *ledger* with nothing to prove it by is refused rather than trusted — the two
halves of the guarantee the whole skill now rests on. Hermetic: one fixture profile,
five fixture ledgers, nothing spawned.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
mkdir -p "$D/sessions" "$D/spawn-agent"
# pid 1 is permanently "alive" for the reason check 3 gives, and it is the only pid
# these records need -- what is being faked is the REGISTRY, not the process table.
printf '{"pid":1,"name":"smoke-own-probe","cwd":"/","sessionId":"11111111-1111-1111-1111-111111111111","messagingSocketPath":"/tmp/cc-socks/1.sock"}\n' > "$D/sessions/1.json"
# Two more records, one name, one minted id between them: the ambiguous case, made
# reachable without racing anything real. A different name from the probe above, so
# the other four fixtures resolve to exactly one record as before.
printf '{"pid":1,"name":"smoke-own-dup","cwd":"/","sessionId":"33333333-3333-3333-3333-333333333333","messagingSocketPath":"/tmp/cc-socks/1.sock"}\n' > "$D/sessions/2.json"
printf '{"pid":1,"name":"smoke-own-dup","cwd":"/","sessionId":"33333333-3333-3333-3333-333333333333","messagingSocketPath":"/tmp/cc-socks/1.sock"}\n' > "$D/sessions/3.json"
printf 'smoke-own-probe\tL1\tL2\tspawned\t11111111-1111-1111-1111-111111111111\t1\tcmux\n'  > "$D/spawn-agent/led-ok.tsv"
printf 'smoke-own-probe\tL1\tL2\tspawned\t99999999-9999-9999-9999-999999999999\t-\therdr\n' > "$D/spawn-agent/led-foreign.tsv"
printf 'smoke-own-probe\tL1\tL2\tspawned\t\t-\tcmux\n'                                      > "$D/spawn-agent/led-legacy.tsv"
printf 'smoke-own-probe\tL1\tL2\tspawned\t11111111-1111-1111-1111-111111111111\t1\tcmux\n'  > "$D/spawn-agent/led-nosidecar.tsv"
printf 'smoke-own-dup\tL1\tL2\tspawned\t33333333-3333-3333-3333-333333333333\t1\tcmux\n'    > "$D/spawn-agent/led-ambiguous.tsv"
# Six columns, no host, sidecar present -- what a mid-upgrade supervisor writes. It
# must still resolve, because only the markdown enforces the width.
printf 'smoke-own-probe\tL1\tL2\tspawned\t11111111-1111-1111-1111-111111111111\t1\n'        > "$D/spawn-agent/led-nohost.tsv"
# Five of the six get a sidecar. Without one they would all stop at the sidecar
# check and never reach the assertion they exist to make.
for f in ok foreign legacy ambiguous nohost; do
  printf '22222222-2222-2222-2222-222222222222' > "$D/spawn-agent/led-$f.owner"
done
```

**The ledgers go in `$D/spawn-agent/`, not in `$D`, and the directory name is the
load-bearing part.** The enforcement sub-check below points `TMPDIR` at `$D`, and the
guard globs `$TMPDIR/spawn-agent/*.tsv` — so this layout is what lets one set of
fixtures serve both halves. It is also what keeps the guard's glob off the **live**
ledgers in the real `${TMPDIR:-/tmp}/spawn-agent/`, which belong to running sessions
and which nothing in this check may read, write or point at.

```bash
O="<plugin root>/skills/spawn-agent/lib/owned.py"
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
for f in ok foreign legacy nosidecar nohost; do
  CLAUDE_CONFIG_DIR="$D" python3 "$O" "$D/spawn-agent/led-$f.tsv" smoke-own-probe >/dev/null 2>&1
  printf '  %-9s -> exit=%s\n' "$f" "$?"
done
CLAUDE_CONFIG_DIR="$D" python3 "$O" "$D/spawn-agent/led-ambiguous.tsv" smoke-own-dup >/dev/null 2>&1
printf '  %-9s -> exit=%s\n' ambiguous "$?"
CLAUDE_CONFIG_DIR="$D" python3 "$O" "$D/spawn-agent/led-ok.tsv" no-row-for-this >/dev/null 2>&1
printf '  %-9s -> exit=%s\n' norow "$?"
CLAUDE_CONFIG_DIR="$D" python3 "$O" "" smoke-own-probe >/dev/null 2>&1
printf '  %-9s -> exit=%s\n' noledger "$?"
```

PASS on exactly:

```
  ok        -> exit=0
  foreign   -> exit=3
  legacy    -> exit=3
  nosidecar -> exit=5
  nohost    -> exit=0
  ambiguous -> exit=4
  norow     -> exit=2
  noledger  -> exit=2
```

**The padding is not decoration.** This block is read under a heading that says
"exactly", so an unpadded `echo` would differ textually from every expected line and
invite a FAIL on a passing run.

**`foreign` is the reported incident, reduced to one line.** The name matches a live
session; the minted id does not. Exit 0 there would mean the ledger trusts a name, and
the run would drive a session it never started.

**`legacy` must fail the same way, not more softly.** That row carries the four-column
format every earlier version of this skill wrote, padded out to the current width — the
minted id in column 5 is the empty one, so there is nothing to check and therefore no
ownership. Read the fixture rather than the name: columns 6 and 7 hold `-` and a host
token like every other row here, because the width is enforced only by the markdown and
a row this file writes has to be one `owned.py` would really be handed. A silent pass
there re-opens the bug for every ledger already on disk.

**`nosidecar` is the same row as `ok` with the sidecar removed, and that is the whole
point of it.** The row is well-formed and names a live session carrying the
minted id — everything `ok` has. What it cannot show is *whose* the ledger is, and
before exit 5 existed it resolved to a working `uds:` address anyway. That is not
hypothetical: a supervisor serving pre-sidecar skill text against these scripts wrote
exactly this file by hand, had every `SendMessage` to that worker gated in both
directions, and was sent by the skill's own diagnostic to a tool that reported nothing
wrong. Exit 0 here is that bug, restored.

**`nohost` must exit 0, and its passing is the assertion.** It is `ok` with column 7
removed — the shape a supervisor serving pre-upgrade skill text writes into its own
ledger, since skill text is snapshotted at session start while `lib/*.py` is read fresh
on every call. Every reader in this plugin indexes the ledger with a length guard and
none checks the column count, so ownership is unaffected by the width; the only thing
that enforces seven is the markdown at the setup block. That is what makes the upgrade
survivable in the direction that matters: a mid-upgrade supervisor keeps resolving its
own workers for its whole life, and it is the *next* session in that slot that refuses
the file loudly, at check 2's gate, rather than acting on rows it cannot attribute.

**`ambiguous` is the one HARD STOP that nothing else here reaches.** Two registry
records, one name, one minted id between them — the state measured on 2026-08-13 when
two live sessions were started with the same `--session-id` and both registered
without error. `owned.py` must refuse to pick, and the fixture is deliberately named
`smoke-own-dup` rather than `smoke-own-probe` so that adding it leaves the other rows
resolving to exactly one record, as they did before.

**`noledger` is the path that used to be a stray file in your repo.** Before the
existence check, an empty first argument was answered as a *missing sidecar*, and the
repair line it printed ended in a bare relative `.owner` — one paste away from
dropping that file wherever the shell happened to be standing. An empty `$LEDGER` is
the ordinary way in, since a Bash call's variables do not survive the call. Exit 2
names the real fault instead.

**And the `ok` row is the regression that matters most.** It must exit 0 *and* print a
`uds:` address, because the reply path depends on it: if ownership is too strict, every
worker becomes unaddressable and the skill stops working rather than stops hijacking.
Note what its sidecar proves and what it does not: under the fixture profile `me.py`
cannot resolve this session at all, so the owner-mismatch branch falls through by
design and the value in the file is never compared. `ok` therefore tests that a
*present* sidecar does not block — which is exactly the property `nosidecar` is
paired against.

### The enforcement layer — it ships, so its absence is a FAIL

A `PreToolUse` hook on `SendMessage` gates sends independently of the skill, and it is
**part of the plugin**: `hooks/spawn-agent-guard.py`, wired by `hooks/hooks.json`. It
used to be an untracked file in one profile's `~/.claude/hooks/`, which is why this
block once announced a SKIP. It no longer may — a missing guard now means the plugin
is incomplete, and the run is trusting prose alone.

First the wiring, which costs nothing and catches the whole class of failure where the
script is fine and nothing ever calls it:

```bash
python3 - "<plugin root>" <<'PY'
import json, os, sys
root = sys.argv[1]
bad = []
hj = os.path.join(root, "hooks", "hooks.json")
if not os.path.isfile(hj):
    sys.exit("  hooks.json     -> FAIL missing at %s" % hj)
print("  hooks.json     -> present")
try:
    cfg = json.load(open(hj))
except ValueError as exc:
    sys.exit("  parses         -> FAIL %s" % exc)
print("  parses         -> ok")
entries = [h for group in cfg.get("hooks", {}).get("PreToolUse", [])
           for h in group.get("hooks", [])
           if "SendMessage" in (group.get("matcher") or "")]
if not entries:
    sys.exit("  matcher        -> FAIL no PreToolUse entry matching SendMessage")
print("  matcher        -> SendMessage, %d command(s)" % len(entries))
for h in entries:
    cmd = h.get("command", "")
    toks = [t for t in cmd.split() if "CLAUDE_PLUGIN_ROOT" in t]
    if not toks:
        print("  command path   -> FAIL names no plugin-root path: %r" % cmd)
        bad.append(True)
    for tok in toks:
        p = tok.strip("\"'").replace("${CLAUDE_PLUGIN_ROOT}", root)
        gone = not os.path.isfile(p)
        print("  command path   -> %s%s" % (p, "   MISSING -- FAIL" if gone else ""))
        bad.append(gone)
    t = h.get("timeout")
    ok = isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 120
    print("  timeout        -> %r%s" % (t, " seconds" if ok else "  FAIL not an int in 1..120 seconds"))
    bad.append(not ok)
sys.exit(1 if any(bad) else 0)
PY
```

PASS on exactly these five lines, and exit 0:

```
  hooks.json     -> present
  parses         -> ok
  matcher        -> SendMessage, 1 command(s)
  command path   -> <plugin root>/hooks/spawn-agent-guard.py
  timeout        -> 10 seconds
```

**A missing line is the failure, which is why the expected output is enumerated rather
than described.** The first version of this block iterated the command's tokens and
printed a `command path` line only for one containing `${CLAUDE_PLUGIN_ROOT}` — so a
`hooks.json` naming an absolute path instead, which is exactly what a bad merge
restoring the old hand-wired `python3 '/Users/ns/.claude/hooks/spawn-agent-guard.py'`
would produce, printed **four lines and exited 0**. It passed by not testing anything.
Absence of a FAIL is not a PASS unless you know how many lines to count; the loop now
records that nothing matched and fails on it.

**The command path is the assertion with teeth.** `${CLAUDE_PLUGIN_ROOT}` is expanded
by Claude Code at execution time, so a typo in it is not a startup error and not a log
line — the hook simply never runs, and a guard that never runs is byte-for-byte
indistinguishable from a guard with no objection. Resolving the token by hand against
the root check 0a derived is the only cheap way to see it.

**The `timeout` is asserted by type and range, not by presence.** It is in seconds, so
a four-digit value is not milliseconds — it is a timeout of over an hour, which behaves
as none at all right up until a hook hangs and takes the session with it. A presence
check prints `timeout -> 10000 seconds` and calls it PASS, committing precisely the
mistake the sentence above it warns about; `1..120` is the range a `SendMessage` gate
can defensibly sit in.

Then the guard's own behaviour, which is the part that matters:

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
G="<plugin root>/hooks/spawn-agent-guard.py"
[ -f "$G" ] || G=~/.claude/hooks/spawn-agent-guard.py   # legacy hand-wired copy
[ -f "$G" ] || { echo "  FAIL no guard at either path -- it ships now"; exit 1; }
echo "  guard: $G"
# 1. A session that owns nothing, sending to the fixture worker -> ask.
printf '{"session_id":"00000000-0000-0000-0000-000000000000","tool_name":"SendMessage","tool_input":{"to":"uds:/tmp/cc-socks/1.sock","message":"x"}}' \
  | TMPDIR="$D" CLAUDE_CONFIG_DIR="$D" python3 "$G" > "$D/ask.out" 2>&1
X=$?
DEC=$(python3 -c 'import json,sys
print(json.load(open(sys.argv[1]))["hookSpecificOutput"]["permissionDecision"])' \
  "$D/ask.out" 2>/dev/null || echo "<no decision -- see $D/ask.out>")
echo "  ask       -> decision=$DEC exit=$X"
```

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
G="<plugin root>/hooks/spawn-agent-guard.py"
[ -f "$G" ] || G=~/.claude/hooks/spawn-agent-guard.py   # legacy hand-wired copy
[ -f "$G" ] || { echo "  FAIL no guard at either path -- it ships now"; exit 1; }
# 2. The SAME send, from the session named in led-ok.owner -> silence.
printf '{"session_id":"22222222-2222-2222-2222-222222222222","tool_name":"SendMessage","tool_input":{"to":"uds:/tmp/cc-socks/1.sock","message":"x"}}' \
  | TMPDIR="$D" CLAUDE_CONFIG_DIR="$D" python3 "$G" > "$D/pass.out" 2>&1
X=$?
[ -s "$D/pass.out" ] && cat "$D/pass.out"     # anything here is the failure, shown
echo "  passthru  -> exit=$X bytes=$(wc -c < "$D/pass.out" | tr -d ' ')"
```

PASS on exactly:

```
  guard: <plugin root>/hooks/spawn-agent-guard.py
  ask       -> decision=ask exit=0
  passthru  -> exit=0 bytes=0
```

**Read the `guard:` line, do not skim past it.** It says which of the two paths answered,
and the plugin one is the only one that passes cleanly: falling through to
`~/.claude/hooks/` means the shipped file is missing and you are testing a legacy copy
that exists on this machine and on no user's. Record that as a FAIL of the *plugin* even
when the two assertions below it are green.

**Assert the decision string, not the byte count, and this is the check correcting
itself.** An earlier version of this block printed `bytes=` on the `ask` line and
called it PASS for anything non-zero. Measured against a stub that is the real guard
with `"ask"` replaced by `"allow"`: the guard answers 356 bytes, the stub 358 — both
non-zero, both accepted, while the paragraph below calls the second a FAIL. A byte
count cannot see the one field this check is about. `decision=allow` is a FAIL; it
would wave through exactly the send the guard exists to catch.

`bytes=` stays on the pass-through line because there 0 **is** the assertion — there
is no decision to name when a guard correctly says nothing.

**Both blocks stop on a missing guard, rather than one announcing the failure and then
running anyway.** The `|| { …; exit 1; }` form is what makes the announcement true.
With `2>&1` capturing the interpreter's own error, a guard at neither path would
otherwise print two unexplained lines instead of the one that names the fault. The
earlier version of this exited **0** with the word SKIP, which was correct while the
guard was an untracked file in one profile and is a false PASS now that it ships.

**Both directions, because `ask` alone is passed by a guard that asks for
everything** — and that is not a straw man, it is the live symptom this check was
extended for: a supervisor whose ledger had no `.owner` was gated on *every*
`SendMessage`, in both directions, by a guard that looked perfectly healthy against
the one-payload version of this check. The second payload is the first with one field
changed, so the pair isolates ownership as the only variable: same target, same
profile, same ledgers, different `session_id`. Silence and exit 0 is the whole
assertion — the guard emits nothing when it has no objection, and any output at all
there means an owned worker is being gated.

**`TMPDIR` is mandatory on both lines, alongside `CLAUDE_CONFIG_DIR`.** The guard finds
ledgers by globbing `$TMPDIR/spawn-agent/*.tsv`, so it is `TMPDIR` — not
`CLAUDE_CONFIG_DIR` — that decides which ownership records it reads at all. Pointing it
at `$D` is what makes the pass-through payload's ownership resolvable, and it is also
what keeps the guard's glob off the live ledgers in the real
`${TMPDIR:-/tmp}/spawn-agent/`, which belong to running sessions and are none of this
check's business.

**`CLAUDE_CONFIG_DIR` is mandatory on both lines too, and leaving it off passes for the
wrong reason.** The guard only interposes on targets it can resolve to a *live registry
record*; everything else it deliberately lets by, because an unresolvable `to` is an
in-process subagent or a teammate and gating those would put a prompt in front of
ordinary work. Without the fixture profile on the path, pid 1 resolves to nothing, the
guard passes through, and the check prints an empty line that reads exactly like "no
hook installed". Verified 2026-08-13 in both forms: silent without the variable, `ask`
with it.

The two gate different halves of the pass-through assertion, so neither is optional
there. `CLAUDE_CONFIG_DIR` makes the **target** resolve; `TMPDIR` makes the
**ownership** resolve. Miss the first and the guard is silent because it sees no peer
— a silence indistinguishable from the one being asserted. Miss the second and it sees
the peer but no proof, and prints `ask` where this check demands nothing.

### The two defects the guard shipped to fix — regression cover

Both were live in the guard on the day it moved into the plugin, and neither is
covered by the pair above. Both are written in the **ask-versus-silence** direction:
each asserts the *absence* of a prompt in a state where the broken version raised one,
or vice versa, so a regression flips a line rather than leaving the output unchanged.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
G="<plugin root>/hooks/spawn-agent-guard.py"
# SELF-SCOPE: a ledger directory holding a .tsv and NO .owner. Nothing in it is
# provably anyone's, so every pass the guard could grant is unreachable and the
# only thing it can add is a prompt -- it must return before it resolves a target.
R="$D/regress-selfscope"
mkdir -p "$R/spawn-agent"
printf 'w\tL1\tL2\tspawned\t11111111-1111-1111-1111-111111111111\t1\n' > "$R/spawn-agent/led.tsv"
printf '{"session_id":"00000000-0000-0000-0000-000000000000","tool_name":"SendMessage","tool_input":{"to":"uds:/tmp/cc-socks/1.sock","message":"x"}}' \
  | TMPDIR="$R" CLAUDE_CONFIG_DIR="$D" python3 "$G" > "$R/out" 2>&1
echo "  selfscope -> exit=$? bytes=$(wc -c < "$R/out" | tr -d ' ')"
```

PASS on `selfscope -> exit=0 bytes=0`. It discriminates: the pre-ship guard answers
**362** bytes of `ask` on this fixture, because it read the `.tsv`, found no sidecar
beside it, proved nothing, and asked — which is what every user who installed the
plugin and never spawned would have got on every `SendMessage`.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/own-fixture"
G="<plugin root>/hooks/spawn-agent-guard.py"
# TMPDIR: a set-but-empty TMPDIR once left the ledger directory as the RELATIVE
# path `spawn-agent`, i.e. the cwd. Same payload from two directories, one of
# which holds a complete ledger + sidecar.
R="$D/regress-tmpdir"
mkdir -p "$R/holds/spawn-agent" "$R/sibling"
printf 'w\tL1\tL2\tspawned\t11111111-1111-1111-1111-111111111111\t1\n' > "$R/holds/spawn-agent/led.tsv"
# The sidecar names the SENDER, and the row's minted id names the target. Both
# must match or the planted ledger grants nothing even to a broken guard, the two
# cwds agree for the wrong reason, and the check passes vacuously.
printf '00000000-0000-0000-0000-000000000000' > "$R/holds/spawn-agent/led.owner"
for c in holds sibling; do
  ( cd "$R/$c" && printf '{"session_id":"00000000-0000-0000-0000-000000000000","tool_name":"SendMessage","tool_input":{"to":"uds:/tmp/cc-socks/1.sock","message":"x"}}' \
    | TMPDIR="" CLAUDE_CONFIG_DIR="$D" python3 "$G" 2>&1 | wc -c | tr -d ' ' \
    | sed "s/^/  tmpdir cwd=$c -> bytes=/" )
done
```

PASS when the two byte counts are **identical**. That is the invariant, stated as the
property rather than as a number: the ledger directory is not the cwd, so the cwd
cannot change the answer. Measured against the pre-fix guard the two lines differ —
**0 from `holds`, 356 from `sibling`**, silence and `ask` for one payload — which is a
file in a repository deciding whether a stranger's send goes through. The shipped
guard answers 0 from both.

**Why identity and not a literal `bytes=0`.** A set-but-empty TMPDIR correctly resolves
to `/tmp/spawn-agent`, and whether *that* directory holds sidecars is a property of the
machine, not of the guard. Both counts are 0 wherever it is absent, as here — but
pinning the number would turn a machine that happens to have one into a FAIL for a
perfectly correct guard, and a smoke check that cries wolf gets read as noise. Record
the two numbers next to the verdict either way.

## 4. The prune, in four directions — *core*

The documented one-liner matches the **name** as a whole field. Three of these four
directions are properties it must keep; the fourth is the regression test for the bug
that made it stop being a `grep`, and it is the one with teeth.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\tS1\t-\tcmux\n' > "$LEDGER"
name=w1
[ -n "$name" ] && { awk -F'\t' -v k=1 -v want="$name" 'NF && $k != want' "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "A: rows=$(wc -l < "$LEDGER" | tr -d ' ') tmp=$(ls "$LEDGER.tmp" 2>/dev/null | wc -l | tr -d ' ')"
```

PASS on `A: rows=0 tmp=0`. This is the last-row case, which on a one-worker run is the
only prune the run ever does. **Read what it proves now, because the premise inverted in
v0.9.1.** Under the old `grep` this direction caught a `&&` joiner — `grep` exits 1 when
it selects nothing, so the `mv` was skipped and a ghost row and a stray `.tmp` survived.
`awk` exits **0** whether or not it selects anything (measured on exactly this fixture),
so that failure is gone and `&&` would pass here too. What A still proves is that the
last row is removable at all and that no `.tmp` is left behind.

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\tS1\t-\tcmux\nw2\tSU-BBB\tPU-BBB\treported\tS2\t-\tcmux\n' > "$LEDGER"
unset name
[ -n "$name" ] && { awk -F'\t' -v k=1 -v want="$name" 'NF && $k != want' "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "B: rows=$(wc -l < "$LEDGER" | tr -d ' ') bytes=$(wc -c < "$LEDGER" | tr -d ' ')"
```

PASS on `B: rows=2 bytes=72` — untouched. An unset variable is reachable by *following*
the skill rather than by ignoring it, since variables die with each `Bash` call and the
prune reads as a standalone command. **This premise also changed.** Under `grep` an
unguarded empty variable installed nothing over the whole file — `rows=0 bytes=0`, exit
0, measured 2026-08-09. Under `awk` an empty `want` keeps every row whose column 1 is
non-empty, so the unguarded form leaves this fixture at `rows=2` as well. The guard is
now refusing a caller that lost its loop variable, not preventing a catastrophe, and B
asserts the guard is still *there* rather than that removing it destroys anything.

The third direction asserts that the one-liner needs **no** host term:

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
LEDGER="$D/prune.tsv"
printf 'w1\tSU-AAA\tPU-AAA\treported\tS1\t-\tcmux\nw3\tw9:p3\tterm_658d\treported\tS3\t-\therdr\n' > "$LEDGER"
name=w1
[ -n "$name" ] && { awk -F'\t' -v k=1 -v want="$name" 'NF && $k != want' "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "C: rows=$(wc -l < "$LEDGER" | tr -d ' ') left=$(cut -f1 "$LEDGER" | tr '\n' ',')"
```

PASS on `C: rows=1 left=w3,` — the cmux row went and the herdr row stayed. Run it once
more with `name=w3` and it must answer `left=w1,`. The name is host-free, so this is now
a property that holds by construction rather than one that depended on two id alphabets
failing to overlap; keep the direction anyway, because "by construction" is a claim and
this is the measurement of it.

The fourth is the regression test, and it is the reason this section exists in its
current form. **Run the control alongside it — an assertion that cannot fail is not an
assertion:**

```bash
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
LEDGER="$D/prune.tsv"
ROWS='w1\tw9:p3\tterm_a\treported\tS1\t-\therdr\nw2\tw9:p30\tterm_b\treported\tS2\t-\therdr\n'

printf "$ROWS" > "$LEDGER"
name=w1
[ -n "$name" ] && { awk -F'\t' -v k=1 -v want="$name" 'NF && $k != want' "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "D: rows=$(wc -l < "$LEDGER" | tr -d ' ') left=$(cut -f1 "$LEDGER" | tr '\n' ',')"

printf "$ROWS" > "$LEDGER"
l1=w9:p3
[ -n "$l1" ] && { grep -v -F "$l1" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }
echo "D-control: rows=$(wc -l < "$LEDGER" | tr -d ' ')"
```

PASS on **`D: rows=1 left=w2,`** and **`D-control: rows=0`**, and both halves are
required. `D` is the fix: `w9:p3` and `w9:p30` are distinct rows and pruning one leaves
the other. `D-control` replays the shipped-until-v0.9.1 `grep -v -F` on the same fixture
and must **destroy both rows while exiting 0** — a valid, non-empty locator, so the
emptiness guard never fires and nothing anywhere reports a fault.

A `D-control` of `rows=1` is a **FAIL of the check, not a pass of the code**: it means
the fixture no longer reproduces the bug, so `D` is asserting nothing. herdr reaches this
collision as soon as a server session has created ten-plus panes, since its pane ids
climb and are never reused; cmux was safe from it by accident, its uuids being
fixed-width and unable to prefix-collide.

## 5. Arm a deaf watcher and wait for `WARN` — *core*

A watcher that matches nothing used to be indistinguishable from a healthy watcher
whose workers are still busy. Prove it now says so. This runs **concurrently** with
everything below — arm it, keep going, and collect its line later.

Name the scratch ledger with this slot's id so the leak proof in check 12 catches
this watcher too.

Create its ledger first, with one row naming nobody:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
[ -n "$CALLER_SLOT" ] || { echo "FAIL empty slot"; exit 1; }
CLPID="<the session pid check 0b printed>"
D="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
mkdir -p "$D"
printf 'smoke-warn-nobody\tL1-X\tL2-X\tspawned\n' > "$D/warn-${CALLER_SLOT}.tsv"
ls "$D"/warn-*.tsv
```

That `ls` is the check on the check: the name it prints must contain your slot id. A
bare `warn-.tsv` means the binding line above was dropped, the file is in the wrong
place, and the `Monitor` below — which takes the slot written out literally — will be
armed on a path that does not exist. That produces `WARN ledger unreadable, watching
nothing:` instead of this check's PASS line, which reads as the exact regression check 5
exists to catch, on a completely healthy watcher.

Then arm the `Monitor` tool with this `command`, **with `<plugin root>` and
`<CALLER_SLOT>` written out literally** — `Monitor` runs its own shell, which never saw
your assignments:

```bash
python3 -u "<plugin root>/skills/spawn-agent/lib/watch-workers.py" \
    "${TMPDIR:-/tmp}/spawn-agent-smoke/<CLPID>/warn-<CALLER_SLOT>.tsv" 1
```

PASS on exactly one line, about 30 seconds in:

```
WARN ledger has 1 row(s), none match a live session name: /…/warn-<slot>.tsv
```

Measured in that form on 2026-08-09. Note the shape of the assertion: **one** line,
naming the "rows match nothing" case, and no second one afterwards. Two `WARN`s, or a
`GONE`, or silence past a minute, are all FAIL. Silence in particular is the exact
regression this exists to catch.

**Give that `Monitor` an explicit timeout, and not a short one — use 180 s.** The command
above names none, while the assertion in the paragraph you just read makes *silence past
a minute* a FAIL. Those two only compose if the watching window comfortably outlives the
minute: a `Monitor` that expires at or near 60 s cannot tell "no `WARN` ever came" from
"the window shut before one was due", and the check stops being decidable in either
direction. The floor is therefore **120 s** — past the FAIL threshold with room left to
observe that no *second* `WARN` follows the first — and 180 s is what to actually pick.
Both green runs on 2026-08-12, one on cmux and one on herdr, chose 180 s independently
and both saw the single line at about 30 s, leaving two full minutes of quiet as
evidence rather than as an unmeasured gap.

Keep the task id. Checks 11 and 12 both need it -- 11 reads its lines, 12 stops it.

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
REPO="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/smoke repo"
mkdir -p "$REPO"
git -C "$REPO" init -q
[ -d "$REPO/.git" ] && echo "PASS scratch repo: $REPO"
```

**The `$CLPID` in that path is what keeps the folder-trust gate reachable, and without
it this procedure quietly stops testing the thing it claims to.** Trust is recorded per
path in the profile's `~/.claude.json` as `hasTrustDialogAccepted`, and it **outlives the
directory** — teardown's `rm -rf` deletes the folder while the trust record stays. So a
fixed scratch path is gated on a machine's *first* run and silently pre-trusted on every
run after that, which retires the gate-clearing half of 7d and 7e and all of 7d's
failure-signature table. Caught on 2026-08-12, when a cmux run registered at `n=0` with
no gate at all and the earlier record was still in the profile — including one under the
pre-rename `…/cmux-spawn-agent-smoke/smoke repo`.

A fresh pid per run means a fresh path, so the gate fires every time. It also means the
profile accumulates one trust record per smoke run; they are inert, and clearing them is
optional housekeeping, not part of this procedure.

**The pid sits on the run directory rather than on the repo name, and that is doing a
second job.** Every scratch artifact this procedure writes — the check 2 and 4 ledger
fixtures, check 3's `fixture-profile/`, check 5's deaf ledger, this repo — lives under
`…/spawn-agent-smoke/$CLPID/`, so two smoke runs on one machine cannot touch each
other's files and teardown removes only its own subtree. That is not hypothetical:
measured 2026-08-12, a cmux run and a herdr run were started minutes apart, the
fixture names were fixed (`empty.tsv`, `ok4.tsv`, `prune.tsv`, `fixture-profile/`), and
the first to finish `rm -rf`'d the shared directory out from under the second — taking
its fixtures, its deaf-watcher ledger and its scratch repo. Nothing broke that time
because the second run had already closed its workers, but the ordering was luck: fired
a minute earlier it would have deleted a live worker's cwd while that worker sat in it.
Check 4 is the one that could have been corrupted invisibly, since its two halves write
different content to the same `prune.tsv`.

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
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
[ -n "$CALLER_SLOT" ] || { echo "FAIL empty slot"; exit 1; }
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
awk -F'\t' '{printf "%d: %d cols:", NR, NF; for (i=1; i<=NF; i++) printf " [%s]", $i; print ""}' "$LEDGER"
```

**Brackets, and no whole-record reference.** The brackets are what make an empty field
visible — this check's own FAIL criterion is "any blank-looking field", which
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
python3 "$P" "<NAME>" name; echo "registry exit=$?"  # name form only -- peer.py serves no address
```

PASS when the ledger holds exactly one **seven-column** row naming your worker — with a
minted uuid in column 5, a literal `-` in column 6, and your host's name in column 7 —
**and** the registry lookup exits 1. That pair is the whole ordering guarantee: a recorded slot
that is not yet running anything. Reversed, the window between the two holds a live
agent no ledger knows about, and a turn that dies inside it orphans the slot forever.

`awk: can't open file …` means no row was written at all — the launch happened without
a ledger, or the path is not the one the setup block built. Either is a FAIL here.

Any blank-looking field is a FAIL, not cosmetics — tab is IFS whitespace, so the
cleanup loop's `read` shifts every column left and the row can never be offered for
closing. Column 5 must not be empty: an absent minted id is a row that can never prove
ownership, and `owned.py` exits 3 on it forever.

**Column 6 must read `[-]`, not `[]`, and this is the field that changed.** Before the
host column the unpinned pid was the last field, so an empty one was stripped as
trailing whitespace and nothing moved; now it is interior, and an interior empty field
collapses. Measured 2026-08-17: the same row written with an empty column 6 reads back
as `pid=[cmux] host=[]`, so every worker looks host-unknown until its pid is pinned in
7d — and a worker that never registers is never pinned, which is exactly the worker
whose slot most needs closing. Check 2's format gate cannot catch it: seven fields are
still counted and the tag is still non-empty, so it exits 0. This bracketed dump is the
only place it is visible.

Check what columns 2 and 3 hold for your host: cmux writes a surface uuid and a pane
uuid; herdr writes a pane id (`w9:p3`) and a terminal id (`term_…`).

**And column 7 must name the host check 1a printed.** This is the inherited-
`CMUX_SURFACE_ID` bug caught from a second angle, independent of 1e's ledger-path
test: a herdr run whose row says `cmux` means the setup block's detection did not run
the precedence table, and every locator in that row is about to be resolved by the
wrong file at teardown. The tag is derived from the same expression that chose your
host file, so disagreement between them is not possible in a healthy run — which is
what makes it worth asserting.

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
O="<plugin root>/skills/spawn-agent/lib/owned.py"
L="<the ledger path the setup block built>"
n=0
until python3 "$O" "$L" "<NAME>" >/dev/null; do
  s=$?
  [ "$s" -ge 3 ] && { echo "STOP owned.py exit=$s -- see its stderr above"; break; }
  sleep 1
  n=$((n+1))
  [ "$n" -gt 20 ] && { echo "not addressable after ${n}s"; break; }
done
echo "loop ended at n=$n"
```

**Exit 3, 4 or 5 here is a FAIL of the run, not of the check.** They mean a live
session answers to your worker's name and is not the session you minted (3), that more
than one answers (4), or that the ledger has no `.owner` beside it (5). The first two
mean the ledger row and the machine disagree about who your worker is; the third means
the row cannot be attributed at all. None improves by waiting.

**5 is the one with a repair rather than a diagnosis**, and it points at the setup
block, not at the worker: the run reached this loop without writing its sidecar. Write
it — `python3 <plugin root>/skills/spawn-agent/lib/me.py sessionId > "${L%.tsv}.owner"`
— and say so in the report, because a run that got here is a run whose setup block was
not followed. `owned.py`'s own stderr names both paths for you.

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
is the whole point of the check.** A screen read is a slot command like any other, so
`SKILL.md` wants 7e's occupant check in front of it; run that first if you are being
strict, and record it once:

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

**First the occupant check — assert it returns 0, and prove the assertion has teeth —
*host*.** `SKILL.md`'s "A slot is not a session" mandates this before every keystroke,
and this is the keystroke it bears hardest on: the worker is gated, so it has
registered nothing, and its ledger row is still unpinned. A check that could only join
on the pinned pid answers **3** here — a hard stop on the run's own worker, forbidding
the very keystroke that would clear the gate. That was the shipped behaviour until
2026-08-17, and this check is what catches it coming back.

**cmux** — the row's minted id, joined against the argv of the `claude` on that tty:

```bash
OC="<plugin root>/skills/spawn-agent/lib/occupant.py"
S="<plugin root>/skills/spawn-agent/hosts/cmux-surface.py"
TTY=$(python3 "$S" "<SURF>" tty)
[ -n "$TTY" ] && echo "tty=$TTY" || echo "FAIL no tty for that surface"
python3 "$OC" "$TTY" "$L" "<NAME>"; echo "occupant exit=$? -- PASS iff 0"
```

Then the control, against the **same tty and the same live process**, differing only
in whose worker the row claims is sitting in it:

```bash
CTL="${TMPDIR:-/tmp}/occupant-control.tsv"
sed 's/<the minted SID>/11111111-2222-3333-4444-555555555555/' "$L" > "$CTL"
python3 "$OC" "$TTY" "$CTL" "<NAME>"; echo "control exit=$? -- PASS iff 3"
rm -f "$CTL"
```

Record both numbers. `0` then `3` is the PASS. `3` then `3` is the pre-fix behaviour
and a FAIL of the plugin, not of the worker. **`0` then `0` is the worse FAIL** — it
means the check is passing everything rather than passing your worker, and an
assertion that cannot fail is not an assertion. Say what the control does and does not
prove: it swaps the *row*, not the process, so it proves the verdict is joined on
column 5 and not merely handed out. It does not prove two `claude`s on one tty are
separated correctly — that is not constructible here without starting a stranger in
the user's own terminal.

**herdr** — `PaneInfo` carries no tty, so `occupant.py` does not apply and its
equivalent is the two-witness check in `hosts/herdr.md` §5. Assert the strong half
(`terminal_id` equals ledger column 3), and give it teeth the same way — compare
against some *other* pane's `terminal_id` and confirm that comparison reports a
mismatch. Then **record, rather than assert, what `agent_session.value` holds while
the worker is still gated.** That witness is set by `pane.report_agent_session`, which
a `claude` parked on the trust dialog has not reached; if it reads empty here, then
herdr's "treat a mismatch as a stop" has the same shape as the cmux defect above and
nobody has measured it. Report the value either way — the measurement is the point.

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
O="<plugin root>/skills/spawn-agent/lib/owned.py"
L="<the ledger path the setup block built>"
CLPID="<the session pid check 0b printed>"
REPO="${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID/smoke repo"
python3 -c '
import os, sys
want, got = sys.argv[1], sys.argv[2]
print("want:", want)
print("got :", got or "(nothing)")
if not got:
    sys.exit("FAIL owned.py printed no cwd -- not registered, not ours, or no .owner (5)")
print("PASS" if os.path.exists(got) and os.path.samefile(want, got) else "FAIL")
' "$REPO" "$(python3 "$O" "$L" "<NAME>" cwd)"
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
 "message": "Your only job is to send one reply message. Do not read files, do not run shell commands, do not investigate anything — but you MUST deliver the reply with the SendMessage tool, and loading that tool first if it is deferred in your environment is part of the job, not a violation of it. Printing the text as output does not count as replying.\n\nThe reply body must be exactly one line and nothing else: SMOKE-OK <the name you launched it with>\n\nSend it to uds:/tmp/cc-socks/<your own pid>.sock — that is the session that sent you this message, and the same address is on this message as its `from`. It is named `<your own name>`; that is a label, not an address, so do not send to the name."}
```

**Every placeholder in that block is yours to fill.** `<pid>` is the worker's, from the
address `owned.py` printed. `<your own pid>` and `<your own name>` are this session's —
`python3 <plugin root>/skills/spawn-agent/lib/me.py` prints your name, your `uds:`
address and your session id in one line, which is exactly the three things a worker
cannot look up about you. `<the name you launched it with>` is
the `-n` name you gave the worker.

**That last one used to say "the name you were launched with", addressed to the worker,
and it was fiction.** Measured 2026-08-12, 2 of 2 across both hosts: a worker has no read
on its own launch name. Both said so unprompted and both guessed — one inferred a name
from the supervisor's socket path and the scratch repo (`smoke-34709`, when it was
launched `smoke-w9pe-a`), the other took a `ListAgents` row and landed on **a different
concurrent run's session**. So the placeholder did not join a reply to a worker; it
invited a cross-run misattribution, and with two workers in flight it joined nothing. The
supervisor writes the name because the supervisor is the only party that knows it.

**Join on the transport, not on the body, when the two disagree.** An incoming
`<cross-session-message>` carries a `from-name` attribute, which is authoritative. The
name in the body is a payload the worker could have got wrong; `from-name` is the
channel it actually arrived on. Record both when they differ — that divergence is a
finding.

Otherwise send that wording rather than a paraphrase, and **in particular do not
compress the prohibition back into "do not read files, run commands, or investigate
anything."** `SendMessage` is a tool call, so a literal-minded worker reads a blanket
"no commands" as "no tools" and satisfies "exactly one line and nothing else" by
*printing* the line as assistant output. The sentence meant to keep the probe cheap then
forbids the one action this check exists to measure. Observed 1-in-2 on 2026-08-12: two
workers received the same message under the old wording — identical but for their own
names in the body and the summary — and one sent while the other printed.

Each clause of the replacement does one job:

- **the required action leads**, ahead of any prohibition;
- **the deferred-tool carve-out** is there because `SendMessage` is not always loaded at
  session start. Where tool schemas are fetched on demand, replying costs *two* calls —
  the fetch, then the send — and a worker told its "only action" is to reply can read
  the fetch as forbidden and print instead. The successful worker in the run above made
  exactly that fetch first;
- **"printing … does not count as replying"** is the explicit carve-out; without it,
  "do not run shell commands" keeps reading as "do not use tools";
- **"the reply *body*"** scopes "one line" to the message rather than to the worker's
  whole turn, which is what made printing look compliant.

**The reply target is a literal `uds:` string, and "use the `from` address" is only the
sentence beside it.** That is a change from earlier versions, and it is the one the
measurements forced. Told to reply by *name*, 0 of 3 workers got through first try. Told
to use `from`, 5 of 5 did, then 1 of 1 under herdr — and then, on 2026-08-12, **0 of 2
across both hosts**, each addressing the supervisor by name anyway and recovering via the
ref in the refusal. A worker holds several plausible ways to address you, and
`SendMessage`'s own guidance leans toward names; a literal address leaves nothing to
choose. Keep the `from` sentence — it is a correct fallback and it costs one clause —
but do not rely on it alone.

Three separate PASS conditions here:

- **`SendMessage` is accepted** against the `uds:` address. A bare name is refused on
  first contact and hands you a ref inline; if you find yourself holding a ref, you
  addressed it by name and that is a FAIL of the procedure, not of the plugin.
- **The reply arrives**, interrupting you, and its body contains `SMOKE-OK`. Match on
  the token, not on the whole body — workers inherit the user's global
  `~/.claude/CLAUDE.md`, so a reply may carry whatever preamble that file makes every
  session emit.

  If it does not arrive, record nothing yet. "No reply" has four causes here and only
  two of them are a FAIL of anything; work the ordered diagnosis at the end of this
  check rather than guessing between them.
- **The reply was accepted on the worker's first attempt.** This is the reply-address
  contract. A refusal costs a round trip on the one message that carries the results, so
  it is silent unless you look.

**Read the refusal out of the worker's transcript, not off its screen — *core*.** That is
a change from earlier versions of this check, and the measurement two subsections down is
why: on cmux the screen read cannot see a refusal that has scrolled, and reports a clean
`0` when it does. The transcript has no viewport, no wrapping and no host in it.

Capture the two fields **while the worker is still alive** — `sessionId` is unreadable
once it exits:

```bash
python3 "$P" "<NAME>" sessionId
python3 "$P" "<NAME>" cwd
```

```bash
python3 - "<the sessionId>" "<the cwd>" <<'PY'
import json, os, re, sys
sid, cwd = sys.argv[1], sys.argv[2]
esc = re.sub(r'[^a-zA-Z0-9]', '-', cwd)
roots = [d for d in os.environ.get('CLAUDE_CONFIG_DIR','').split(':') if d] or [os.path.expanduser('~/.claude')]
path = next((p for p in (os.path.join(r, 'projects', esc, sid + '.jsonl') for r in roots)
             if os.path.exists(p)), None)
if not path:
    sys.exit('no transcript for %s under %s -- either that worker has taken no turn at '
             'all, or its transcript lives under a profile not in roots' % (sid, roots))
sends = refused = 0
for line in open(path):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    c = (r.get('message') or {}).get('content')
    if not isinstance(c, list):
        continue
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get('type') == 'tool_use' and b.get('name') == 'SendMessage':
            sends += 1
        elif b.get('type') == 'tool_result' and \
                'is not an agent in this conversation' in json.dumps(b.get('content')):
            refused += 1
print('SendMessage calls=%d  refusals=%d' % (sends, refused))
PY
```

**That "no transcript" exit names two causes because its condition has two.** The same
branch fires when the worker has genuinely taken no turn *and* when the transcript exists
but sits under a profile that is not in `roots`; a missing path cannot tell them apart,
and the earlier wording — "that worker has taken no turn at all" — was flatly more
confident than the test above it. In practice the first cause is the one you have, because
`peer.py` builds its roots with the same `CLAUDE_CONFIG_DIR`-then-`~/.claude` logic, so a
worker it just resolved a `sessionId` for is a worker whose profile is in this list. Which
means the second cause is not a shrug: seeing it against a name `peer.py` answered for is
itself a finding, and `CLAUDE_CONFIG_DIR` is the first thing to read before believing a
turn count of zero.

**Read the two numbers together** — they separate the three outcomes this check exists to
tell apart, and all three were produced on this machine on 2026-08-12:

| result | what happened | verdict |
| --- | --- | --- |
| `calls=0 refusals=0` | it never called the tool; it printed | FAIL the **second** PASS condition — work the row-one note below |
| `calls=1 refusals=1` | the ref wall; it addressed you by name | FAIL the **third**, not the second |
| `calls>=1 refusals=0` | it sent, first try | PASS |

**A clean run can only ever produce the third row, by construction — so a green check 8 is
not evidence that this detector discriminates.** A run that passes is a run whose worker
sent on its first try, which is `calls>=1 refusals=0` and nothing else; the two FAIL rows
describe outcomes the run did not have and therefore did not test. Read a green result as
"it fires correctly on the healthy case", never as "it can tell the three apart". The
discrimination rests entirely on recorded controls — which do exist, in two forms:

- **Against a real refusal.** A worker was told to `SendMessage` to a bare name
  deliberately and not to retry; it was refused, and this read returned
  `calls=1 refusals=1`. The print-only shape is measured the same way — the throwaway
  worker in the scrollback experiment below never called the tool and returned
  `calls=0 refusals=0`.
- **Against three synthetic transcripts.** A cmux run on 2026-08-12 wrote one `.jsonl` per
  row — no call, one call with a refusing `tool_result`, one clean call — and ran this
  check's own detector body over each, getting exactly `calls=0 refusals=0`,
  `calls=1 refusals=1` and `calls>=1 refusals=0`. That is the cheap control, it needs no
  worker, and it is the one to repeat whenever the detector body is edited.

A smoke run cannot provoke a real refusal without breaking the very thing it is measuring,
which is why those numbers are recorded here instead of being re-measured every run.

**Do not grep the raw `.jsonl` instead.** The phrase is ordinary prose and lands in the
file whenever anyone *writes about* the ref wall — a supervisor whose task text mentions
it, a worker narrating what happened. Measured on the same transcript: a plain
`grep -c` returned **4** where the structured read returned **1**, the other three being
the phrase in message text. Match on a `tool_result`, which is the only place the
product itself puts it. (The `⎿` glyph is terminal rendering and is never in the file;
grepping for it there returns nothing on a perfectly healthy send.)

### Why not the screen — cmux saturates at the viewport, silently

**Measured 2026-08-12 in a cmux surface**, against a throwaway worker that emitted 120
uniquely marked lines as assistant text and had exactly one real refusal in its
transcript. Viewport was 55 rows:

| read | lines | of 120 marked lines | refusals |
| --- | --- | --- | --- |
| `read-screen` (no flags) | 55 | 47 — from marker 074 | 0 |
| `read-screen --scrollback` | 55 | 47 — from marker 074 | 0 |
| `read-screen --lines 40` | 40 | 32 — from marker 089 | 0 |
| `read-screen --lines 200` | 55 | 47 — from marker 074 | 0 |
| `read-screen --lines 600` | 55 | 47 — from marker 074 | 0 |
| `read-screen --scrollback --lines 2000` | 55 | 47 — from marker 074 | 0 |
| `capture-pane`, same three flag forms | 55 | 47 — from marker 074 | 0 |
| **the transcript** | — | — | **1** |

**Every form returns the viewport and nothing more.** `--scrollback` adds nothing,
`--lines` only ever *truncates* (40 gave less than the default did), and raising it to
2000 changes not one line. `cmux capture-pane` is the same command wearing a tmux name
and saturates identically. So the old read here — `--scrollback --lines 200` — returned
`lines=55 refusals=0`, which satisfied **both** of the old PASS conditions while a
genuine ref-wall refusal sat in that worker's history. A false green, in exactly the
class of the hard-wrap bug fixed on the herdr side, reached from a different direction.

**The flags are not broken, and that is the point — it is the alternate screen.** Control
run in the *same surface*, immediately after `/exit` dropped it back to a plain shell,
with 120 marked lines printed by `seq`:

| read | lines | of 120 marked lines |
| --- | --- | --- |
| `read-screen` (no flags) | 33 | 32 — from marker 089 |
| `read-screen --scrollback` | 128 | **120 — from marker 001** |
| `read-screen --lines 40` | 40 | 39 — from marker 082 |
| `read-screen --lines 200` | 128 | **120 — from marker 001** |

So `--scrollback` works perfectly on a normal-screen program and reaches every line.
Claude Code runs on the **alternate screen**, which has no scrollback to read — the same
property herdr's own error message names when it says alternate-screen history "can only
be captured by scrolling while idle". herdr solved it by scrolling; cmux exposes no
command that does, so on cmux there is nothing to fix in the read and the transcript is
the answer.

`cmux read-screen --help` also documents that **`--lines <n>` implies `--scrollback`**, so
the two flags are one control, not two — do not read a difference between them into a
result.

### The screen reads, kept as diagnosis only

They are still how you answer "what is on that worker's screen right now" for the
ordered diagnosis below, and for anything you have to *look* at:

```bash
cmux read-screen --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF>" --scrollback --lines 200
```

```bash
# herdr: only once the worker is idle -- see below
herdr agent read "<NAME>" --source recent-unwrapped --lines 200
```

Two things still govern any *matching* you do against that output.

**Claude Code hard-wraps a tool result**, so the phrase is split across rendered lines and
no line-oriented `grep` can ever see it whole. Join the lines and squeeze the runs of
indentation the wrap inserts, or the joined text reads `…in this   conversation…` and a
fixed pattern misses it. Prove the pattern can fire before trusting a zero from it:

```bash
printf '%s\n' "'w' is not an agent in this" "conversation. Re-send with the ref" \
  | tr '\n' ' ' | tr -s ' ' | grep -o "is not an agent in this conversation" | wc -l | tr -d ' '
```

Must print `1`. Measured 2026-08-12 against a worker that had genuinely been refused, the
old `grep -c` form returned `0` and this one returned `1`; `--source recent-unwrapped`
does not rescue the old form, because it joins *soft* wraps and this is a hard one. Any
run that recorded a `0` from the plain `grep -c` proved nothing, and that includes the
runs behind the 5-of-5 figure quoted above.

**And on herdr the read itself has two failure modes, only one of them loud.**

- **Too early is loud.** `recent`/`recent-unwrapped` refuse outright while the agent is
  working — `{"error":{"code":"agent_not_idle",…"its alternate-screen history can only
  be captured by scrolling while idle"}}`. Wait for idle and retry; that error is not a
  FAIL of anything. Note it is a single line of JSON, so a run that matched against it
  would read `lines=1 refusals=0` — a failed read wearing a pass's clothes.
- **The wrong source is silent.** Substituting `--source visible` because it works while
  busy returns a clean `0` having never looked at the line in question. Measured against
  a worker that had emitted 120 marked lines, `visible` returned 46 matches at
  `--lines 200` and 46 again at `--lines 600` — it saturates at the viewport and says
  nothing about it, while `recent-unwrapped` returned all of them.

That `visible` saturation and the cmux one above are the same failure on two hosts — but
notice what it is a failure *of*: the **wrong source**. It is not the herdr argument for
the transcript, because on herdr the right source genuinely reaches.

**`recent-unwrapped` does not saturate, so do not carry the cmux argument over to
herdr.** Re-measured 2026-08-12 on a real worker: it returned 2456 bytes — the complete
turn, agreeing with the transcript line for line. Saturation is settled on this host, and
the case for reading the transcript anyway is three other things, none of them a viewport:

- **It only answers while idle.** `recent`/`recent-unwrapped` refuse outright mid-turn, as
  the loud failure above describes. So the natural moment to ask "was the reply refused" —
  while the worker is still working, which is when you notice nothing has arrived — is
  exactly the moment the read is unavailable. The transcript has no such window; it is
  append-only and readable at any instant.
- **What it shows you is a rendering.** The send appears as the `summary` argument drawn
  into `⎿ … → uds:…`, which is presentation, and presentation is the product's to change
  at any release without anything here breaking loudly. A `tool_use` block named
  `SendMessage` is the product's own structure, and the structure is what the check should
  be pinned to.
- **It cannot count sends at all, only refusals.** A refusal leaves a matchable phrase on
  screen; a *send that never happened* leaves nothing to match. So the screen is blind to
  check 8's **second** PASS condition — the print-instead-of-send failure, the one measured
  1-in-2 on 2026-08-12 — and `calls=0` is a number only the transcript can produce.

Between the two hosts, then: on cmux the screen read is broken and cannot be fixed from
here, and on herdr it works and still answers the wrong question. That is why the PASS
condition rests on the transcript on both.

### If the reply never arrives — the ordered diagnosis

Four causes, worked in this order because the first costs nothing and the last costs a
screen read:

1. **The watcher's lines, first.** `ATTN` means the message is being **held** for
   approval, which is what a permission-class mismatch looks like from here (see check
   1d) and is not a messaging failure at all. `GONE` means the worker died, which is
   check 9's result and not this one. Either way you are done here.
2. **Then the transcript read above**, which answers the single question here — whether a
   `SendMessage` call happened *at all* — as `calls=0` versus `calls>=1`, and does it
   without a viewport in the way. Go to the screen only for what the numbers cannot show
   you: what the worker actually wrote, and whether it is still mid-turn. The same three
   shapes read on screen:

| On the worker's screen | Cause | Record |
| --- | --- | --- |
| `SMOKE-OK …` as plain assistant output, **no `SendMessage` call above it** | it printed instead of sending | depends on what you sent — see below |
| a `SendMessage` call whose result reads `is not an agent in this conversation` | the ref wall; it addressed you by name | FAIL the **third** PASS condition, not the second |
| a `SendMessage` call showing `⎿ … → uds:/tmp/cc-socks/<pid>.sock` | it really sent; the loss is on your side | FAIL, and re-read check 1d |
| none of these, and the worker is still working | you looked too early | wait for idle and read again — not a result |

**That `⎿` is terminal rendering, not file content.** The commands above read a screen,
which is the only place it appears. Go to the session `.jsonl` instead and the same
evidence is a `tool_use` block named `SendMessage`; the glyph is never in the file, for
any worker, so grepping it there returns nothing on a perfectly healthy send.

**Row one has two verdicts, and separating them is the point of this whole note.**
Compare what you actually sent against the block at the top of this check:

- **You paraphrased it, or dropped the carve-out.** The probe's wording failed, not the
  plugin. Re-run the check with the block as written; the run's verdict for check 8 is
  the re-run's, and the first attempt goes in the signal column with the wording you
  used.
- **You sent the block verbatim and it still printed.** That is a genuine FAIL of the
  second PASS condition, and the most interesting thing this check can produce. Record
  FAIL, quote the message, and say so plainly — the wording above is the current
  mitigation for a model behaviour, not a guarantee, and its failing is precisely what
  this note exists to surface rather than absorb.

## 9. `DONE`, from the run's own watcher — *core*

PASS when the run's watcher prints `DONE <NAME>`.

**Expect it after the reply, not before.** The worker sends its reply as its last act
and delivery is immediate, while `DONE` waits on the next poll — up to a poll interval
later. `DONE` arriving *first*, or arriving with no reply at all, is still a PASS for
the watcher and a FAIL for check 8.

**A fresh worker emits one `DONE` before you have sent it anything, and it is not
yours.** Its boot ends as a `busy → idle` transition like any other, so the watcher
reports it. Observed 2026-08-12 on herdr: the first `DONE <NAME>` in the log landed right
after the worker registered, ahead of any task. Harmless, and it is *not* a case of "the
watcher fabricates a completion" — first sight of `idle` is silent by design, so this is
a real transition it genuinely saw. But a supervisor reading "the first `DONE` means my
task finished" will act on a worker that has not read the task yet. Match `DONE` against
the reply you were owed, not against its position in the log.

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

## 11. The blocked-worker signals — `ASK`, `ATTN`, `CLEAR`, `GONE` — *core, host commands*

Check 5 proves `WARN` and check 9 proves `DONE`. The watcher's four remaining lines have
never been proved by this suite at all — they are asserted from hand measurements taken
elsewhere. One of them is load-bearing twice over: check 1d exists to stop a
permission-class mismatch, and check 8's diagnosis tells you to read `ATTN` as "the
message is being held, not lost". Nothing here has ever made an `ATTN` appear, so that
branch has never been observed to work in a run.

This check makes each of them fire on purpose.

**It runs before teardown and it uses its own worker.** Before, because every line below
comes from the run's watcher and teardown stops it. Its own worker, because 11c
deliberately kills one, and killing the worker checks 7–10 rest on would destroy their
evidence.

**Write its ledger row before you launch it**, exactly as 7b requires. This worker is as
real as any other and check 12 must find it.

### 11a. `ATTN` — a worker parked on a permission prompt

**Place a new slot for it first, by the host file's fan-out rule** — the same way check 7
placed its worker. Do **not** reuse check 7's worker's slot: on herdr `agent start`
refuses an occupied pane with `{"error":{"code":"agent_pane_busy",…}}` at exit 1
(measured 2026-08-12 — it fails safe and loudly, but it fails), and on cmux you would be
typing a shell line into a running `claude`.

```bash
# cmux: another tab in this run's agents pane -- no second split
cmux new-surface --workspace "$CMUX_WORKSPACE_ID" --pane "<this run's agents pane>" --type terminal --focus false
```

```bash
# herdr: its own tab, as always here
herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "<REPO>" --label "<NAME2>" --no-focus
```

Resolve both locators from the new slot and **write the ledger row**, exactly as 7b
requires, before anything is launched. Then launch it into the scratch repo you already
trusted in 7e — so no trust gate can fire and the only thing that can block it is the
prompt you are about to cause. The permission class is the point, so pass it explicitly:

```
cmux:  cmux send --workspace "$CMUX_WORKSPACE_ID" --surface "<SURF2>" "cd \"<REPO>\" && claude -n <NAME2> --permission-mode manual\n"
herdr: herdr agent start <NAME2> --kind claude --pane "<the NEW pane id>" -- -n <NAME2> --permission-mode manual
```

Wait for the registry as usual, then give it something that must ask:

```
{"to": "uds:/tmp/cc-socks/<its pid>.sock",
 "summary": "smoke - block on a permission prompt",
 "message": "Run this exact shell command and reply with its first line: ls /usr/share/dict\n\nSend the reply to uds:/tmp/cc-socks/<your own pid>.sock — that is the session that sent you this message, and the same address is on this message as its `from`."}
```

PASS on **`ATTN <NAME2>`** from the run's watcher. Measured 2026-08-12 on herdr: 7.07 s
from sending the task to the registry recording the block, and 3.9 s in an earlier
probe.

**That interval is model latency, not plugin latency, so do not treat it as a
timeout.** The worker has to read the task and decide to run the command before anything
can block; a slow turn is a slow turn. Ten seconds is a reasonable place to look again,
not a deadline — the failure signature is silence at *twenty*, below.

Corroborate from the registry rather than trusting the line alone — this pair is what
decides whether `ATTN` means what the skill claims:

```bash
O="<plugin root>/skills/spawn-agent/lib/owned.py"
L="<the ledger path the setup block built>"
python3 "$O" "$L" "<NAME2>" status
python3 "$O" "$L" "<NAME2>" waitingFor
```

PASS on `waiting`, and a `waitingFor` that does **not** contain the word `input`. That
substring is the entire discriminator — `watch-workers.py` prints `ASK` when
`waitingFor` contains `input` and `ATTN` otherwise — so a permission prompt reported as
`ASK` means the two have been transposed and check 8's diagnosis has been pointing at
the wrong branch all along.

**Read `status` first, and interpret `waitingFor` only against a `status` that exited
0.** `waitingFor` is absent from the record whenever the worker is not blocked, and
`owned.py` exits 1 on an absent field — but it also exits 1 for a worker that does not
exist, and the two are **byte-identical**. Measured 2026-08-12:

| | `status` | `waitingFor` |
| --- | --- | --- |
| live worker, blocked | `waiting`, exit 0 | `permission prompt`, exit 0 |
| live worker, not blocked | `idle`, exit 0 | nothing, exit 1 |
| worker that died, or a name that never existed | nothing, exit 1 | nothing, exit 1 |
| ledger with no `.owner` beside it | nothing, exit **5** | nothing, exit **5** |

So `waitingFor` exit 1 on its own means either "you looked too early" or "the worker is
gone", and nothing distinguishes them. `status` does: exit 0 says the worker is alive and
you are early, exit 1 says it is not there at all and you should be reading check 12's
leak proof instead of hunting a phantom block. This is the same "absent and refused look
identical" shape check 3 is built around, one field over.

**The last row is the one that says nothing about the worker at all**, and on stdout it
is indistinguishable from the other two. Every field of every row answers that way when
the sidecar is missing, so a *whole* column of blanks is the tell — and the exit code
and `owned.py`'s stderr are where the reason is written. Capture the status separately
rather than reading `${var:-}` defaults, or a run whose setup block skipped the sidecar
reads as a machine full of dead workers.

**Silence and `GONE` are both failures here, and they mean opposite things.** Silence
past twenty seconds means the watcher never saw the wait: check that this worker's row
is in the ledger the watcher was actually armed on, which is check 5's failure mode
reappearing. `GONE` means it died instead of blocking.

**`ATTN` alone does not prove your command caused the block — read the screen before
recording it.** A *held peer message* parks a worker at exactly the same
`waiting` / `permission prompt` pair (see check 1d and the skill's permission-class
section), so a cross-class hold and a genuine tool prompt are indistinguishable from the
registry. Caught and ruled out by hand on cmux 2026-08-12: the transcript showed the task
message rendered rather than held, and the block was a real
`Bash command / ls /usr/share/dict / Do you want to proceed?` dialog. Confirm the dialog
is on screen and is the one you provoked.

**That dialog has three options, and it is not the trust gate.** Measured on both hosts:
`1. Yes`, `2. Yes, allow reading from dict/ from this project`, `3. No`, with `❯` already
on 1. So `enter` alone is still the whole answer and the "never send `down` first" rule
from 7e still holds — but for a different reason. On the trust gate option 2 is
"No, exit"; here option 2 grants a *standing* permission you did not intend. Both are
wrong to land on, which is why the rule is "read the screen and count the `❯` row"
rather than a memorised keystroke.

On herdr, wait on it directly as well — faster than polling, and evidence *alongside*
the watcher line rather than instead of it (`SKILL.md`, "A host that publishes its own
agent states does not replace this"):

```bash
herdr agent wait "<NAME2>" --until blocked --timeout 30000
```

**And this check is where that "alongside, never instead" rule earns its keep, because
here the host is measurably coarser than the registry.** herdr reports plain `blocked`
for *both* kinds of block — it does not distinguish a permission prompt from an
`AskUserQuestion`. Measured 2026-08-12: `agent wait` returned `blocked` for 11a and again
for 11b, identically, while `waitingFor` read `permission prompt` and then `input needed`.
So herdr can tell you *that* a human is needed and never *which* signal it is, and
`waitingFor` is the only thing that splits `ATTN` from `ASK`. The two agreed at every
point in that run, in both directions; agreement is what you want to record, not a
substitute for the registry read.

### 11b. `ASK` — a worker waiting on `AskUserQuestion`

Clear 11a's prompt first. **Read the screen before pressing anything**, exactly as 7e
insists — a permission dialog's second option is not always harmless:

```bash
cmux send-key --workspace "$CMUX_WORKSPACE_ID" --surface "<NAME2's ref>" enter
herdr agent send-keys "<NAME2>" enter
```

Let that turn finish, then ask for the other kind of block:

```
{"to": "uds:/tmp/cc-socks/<its pid>.sock",
 "summary": "smoke - block on AskUserQuestion",
 "message": "Call the AskUserQuestion tool exactly once, asking whether to proceed with option A or option B. Do no other work and read nothing; just ask, and wait for the answer."}
```

PASS on **`ASK  <NAME2>`** — not `ATTN`. Note the **two** spaces: the watcher pads `ASK`
to align with `ATTN`/`DONE`/`GONE` (`print(f"ASK  {name}")`), and this check invites
matching the literal string. Then the same registry pair, and this time `waitingFor`
**must** contain `input` — measured on both hosts 2026-08-12 as literally `input needed`.

Those two lines are the whole content of this check: one `waiting` status routed to two
different signals by a single substring test, observed going both ways within one run,
on one worker.

**And that substring is more fragile than it looks — record the literal `waitingFor`
strings, not just which signal fired.** The discriminator is a bare "is `input` anywhere
in this value" test. `permission prompt` merely happens not to contain the word. A future
registry value like *"input needed for permission"* would route a permission prompt to
`ASK` silently, with nothing anywhere reporting a fault — and the two strings this check
recorded are the only evidence a later reader would have that the split ever worked.

### 11c. `CLEAR` or `DONE`, and then `GONE`

Answer or dismiss the question from 11b and record **which line the watcher prints
next**. The two are a real distinction and only one of them is guaranteed:

| What follows | Means |
| --- | --- |
| `DONE <NAME2>` | it took a turn after unblocking — the normal case, and a PASS |
| `CLEAR <NAME2>` | it unblocked without taking a turn, so nothing is coming and the task would need re-sending |

**Record which you got; do not chase the other.** `CLEAR` is the one line this suite
still cannot produce on demand — it needs a block that ends with no turn behind it, and
whether dismissing a question does that is not something this check controls. Seeing
`DONE` proves the unblock is *reported*, which is the property check 9 depends on. Write
down which line appeared rather than recording "11c PASS" on its own; a suite that
cannot say which of two signals it saw is not measuring the difference between them.

Then kill it, **with the watcher still armed**. On herdr the ledger holds the worker's
*pane*, so derive the tab first — `["result"]["pane"]["tab_id"]`, the accessor
`hosts/herdr.md` §9 warns about, since the shortened form raises `KeyError`:

```bash
cmux close-surface --workspace "$CMUX_WORKSPACE_ID" --surface "<NAME2's ref>"
```

```bash
TAB=$(herdr pane get "<NAME2's pane>" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["tab_id"])')
herdr tab close "$TAB"
```

PASS on **`GONE <NAME2>`**, and bound it at **two polls** rather than at a wall-clock
number — the watcher requires two consecutive absences before calling a worker dead
rather than merely missed (`if misses[name] < 2: continue`, default poll 2.0 s). Measured
2026-08-12: **3.83 s** on herdr, sampling the Monitor output at 0.2 s; a cmux run
sampling coarsely could only bracket the same event at ≤8.2 s. A few seconds is the
expectation; anything past a minute is the failure.

**This is the only place `GONE` is ever observed, and that is why it lives here.** Check
12 stops every monitor first, so nothing is listening when its slots close — measured on
both hosts 2026-08-12, where two full runs closed four workers between them and saw not
one `GONE` line.

Finally, prune `<NAME2>`'s row from the ledger, using the documented one-liner check 4
exercises, so check 12 does not offer to close a slot that is already gone.

## 12. Teardown, and proof that nothing leaked

**Stopping the watcher first is deliberate, and it costs you the `GONE` lines.** Closing
slots under a live watcher would emit one `GONE` each, which is benign but arrives mixed
into the report you are writing; check 11c already observed `GONE` once, on purpose,
which is what that signal needed. So stop the monitors, then close.

1. **`TaskStop` every monitor this run armed** — normally two, the run's watcher and the
   deaf one from check 5. A partial re-run that skipped check 5 has only one, and a
   check-5 watcher that already hit its own timeout is gone rather than leaked:
   `TaskStop` answering `No task found with ID: …` is that case, not a failure. Step 5's
   scoped `pgrep` is what settles it either way — count monitors there, not here.
2. **Close each slot in the ledger**, and only those:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
[ -n "$CALLER_SLOT" ] || { echo "FAIL empty slot -- do not close anything"; exit 1; }
LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
while IFS=$'\t' read -r name l1 l2 state sid pid host; do
  [ -n "$l1" ] || continue
  case "$l1" in "$CMUX_SURFACE_ID"|"$HERDR_PANE_ID") echo "REFUSING to close the caller: $name"; continue;; esac
  echo "$name  host=$host  l1=$l1  l2=$l2  state=$state  resume=$sid"
done < "$LEDGER"
```

   **Every row this smoke run wrote must carry your own host in column 7**, because
   this test is single-host by design. A row tagged otherwise is a bug in the run
   itself, not a cross-host worker: report it, and close nothing. The `case` line above
   stays as it is — it is already the cross-host-aware guard in this block, refusing
   the caller's own slot under either host's variable.

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

4. **Delete the ledger file and its `.owner` sidecar**, not just the rows, and the scratch fixtures. The sidecar is the one a teardown misses — it is not the ledger, so it survives a cleanup that looks complete:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
CLPID="<the session pid check 0b printed>"
[ -n "$CALLER_SLOT" ] || { echo "FAIL empty slot -- the real ledger would survive this"; exit 1; }
[ -n "$CLPID" ] || { echo "FAIL empty CLPID -- this would rm -rf every run's scratch"; exit 1; }
rm -f "${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv" \
      "${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.owner"
rm -rf "${TMPDIR:-/tmp}/spawn-agent-smoke/$CLPID"
ls "${TMPDIR:-/tmp}/spawn-agent/" 2>/dev/null; echo "  (your slot's .tsv must be gone)"
```

   The second line removes **this run's** scratch directory and nothing else — the
   throwaway repo, the ledger fixtures, the deaf watcher's ledger, and check 3's
   `fixture-profile/`. Two things under it could be misread as real by another tool,
   so confirm both are gone: `fixture-profile/` is a synthetic `CLAUDE_CONFIG_DIR`
   holding a session record for a session that never existed, and check 3b's
   `own-fixture/` now contains a directory literally named `spawn-agent/` holding
   five ledgers and four sidecars. Nothing globs for those outside a `TMPDIR` pointed
   at them, which is why they are safe to write — and also why they must not be left
   lying in a tree that a future check might point a `TMPDIR` at.

   **Both guards are load-bearing and they protect different strangers.** An empty
   `CALLER_SLOT` makes the first line delete `…/spawn-agent/.tsv` and quietly leave the
   real ledger; an empty `$CLPID` makes the second line `rm -rf` the whole
   `spawn-agent-smoke/` tree, which is a *concurrent* run's live scratch — including a
   worker's cwd while that worker is sitting in it. Neither line touches the real ledger
   directory, and neither should ever touch another run.

5. **No watcher survived**, scoped to your own slot:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- refusing an unscoped pgrep"; exit 1; }
pgrep -fl "watch-workers.py.*${CALLER_SLOT}"
```

   PASS on no output. **Do not run this bare** — `pgrep -fl watch-workers.py` lists
   every watcher on the machine, including live ones belonging to other sessions, and
   an agent following that reported two healthy watchers as orphans, one of them its
   own supervisor's.

   **The binding and the guard above are what keep "bare" from happening by accident,
   and this is the one place where the accident is destructive.** `CALLER_SLOT` does not
   survive from the block that set it, so without them the pattern collapses to
   `watch-workers.py.*` — the forbidden form, reached silently. Teardown then reports
   every watcher on the machine as this run's leak, which the verdict rules turn into a
   FAIL on a clean run; and the remedy below, run the same way, kills every other
   session's watcher including your own supervisor's. If a line does come back it is
   yours and it is stuck:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux -- or CALLER_SLOT="${HERDR_PANE_ID//:/-}"
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- refusing to pkill unscoped"; exit 1; }
pkill -f "watch-workers.py.*${CALLER_SLOT}"
```

6. **Topology is back to baseline** — re-run the counter from check 7 and compare with
   the numbers you recorded. Same counts. Anything left over is a leak, and the ledger
   you just deleted was the record of what it was, so say so explicitly rather than
   hunting for it later.

## The verdict

Report a table — check number, PASS / FAIL / SKIPPED / DENIED, and the literal signal.
Then one line of overall judgement, which is not a percentage:

- **PASS** — every check passed, or the only non-passes are SKIPPED with a stated
  reason. The plugin works on this machine, on this host.
- **PARTIAL** — checks 0 through 5 passed and something in 7 through 11 did not. The
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
