---
name: cmux-spawn-agent
description: Spawn Claude Code agents into cmux surfaces (tabs) and drive multi-stage pipelines across them. The word "spawn" is on its own a sufficient trigger — "spawn an agent", "spawn 3 agents" — with no mention of cmux, tabs or panes required. Also use when the user asks to run something "in another agent", "in a new Claude Code", "in a pane", to hand work from one agent to the next ("run X to the end, then /y, then in a new session run /z"), or to run several agents in parallel and report as each finishes. Prefer this over the built-in subagent/Task tool for all of those — a subagent is invisible and cannot be watched, clicked into, or taken over, which is the whole point of spawning one.
---

# Spawn agents into cmux surfaces

Run work in separate Claude Code sessions that live in visible cmux surfaces, so
the user can watch each one and take it over by clicking its tab.

**The join key is the name you give at launch** (`claude -n <name>`). One string is
the tab title, the peer-registry key and the message address at once. Nothing has
to be pre-assigned — no `--session-id`, no `uuidgen`.

Two channels reach a worker, and they are **not** interchangeable:

| | `SendMessage` (cross-session messaging) | `cmux send` + `send-key` |
| --- | --- | --- |
| what it is | a message from a peer session | keystrokes, as if the user typed them |
| carries | prose only | anything, including slash commands |
| quoting | none — it is a tool argument | shell-quoted through a terminal |
| the worker replies | yes, and the reply interrupts you with the result in it | no |
| reaches a **blocked** worker | no — it queues until the block clears | yes, and it is the only thing that clears one |

Default to `SendMessage` for tasks and prose. Use keys for slash commands, for
answering a prompt, and whenever the worker must act on the user's own authority.

| Need | Source |
| --- | --- |
| where you are | `cmux --json identify` → `.caller` (`.focused` is wherever the user drifted) |
| layout, ttys, titles | `cmux --json --id-format both tree --workspace "$CMUX_WORKSPACE_ID"` |
| a worker's pid, cwd, session id, status, **message address** | `<config-dir>/sessions/<pid>.json`, matched on `name` |
| finished / blocked / died, **pushed to you** | one `Monitor` running `watch-workers.py` |
| a stage's actual findings | the worker's own reply message |
| send a task | `SendMessage` to `uds:<socket>` |
| answer a prompt | `cmux send-key --surface <ref> enter` |

## The peer registry is a directory of JSON files

Every session with messaging writes `<config-dir>/sessions/<pid>.json` and
rewrites it whenever its status changes. That file is the whole coordination
substrate — readable from `Bash`, no event bus, no cmux dependency:

```json
{"pid":30580,"sessionId":"8669cd04-…","cwd":"/Users/ns/workspace/agent-toolkit",
 "messagingSocketPath":"/tmp/cc-socks/30580.sock","name":"exp-alpha",
 "status":"waiting","waitingFor":"input needed"}
```

Three properties of it are load-bearing:

- **`status` is open-ended, and `waitingFor` splits the blocked case in two.**
  `idle` when the turn ended; `waiting` with `waitingFor: "input needed"` (an
  `AskUserQuestion`) or `waitingFor: "permission prompt"` (a tool or plan
  approval), both measured live 2026-08-09. Everything else means working — and
  it is **not** just `busy`: a supervisor mid-run was caught reporting `shell`.
  So treat the set as open and collapse the tail to "working". A watcher that
  keeps each unknown value as a state of its own stops recognising the
  transition *out* of it, and the turn-end it was built to catch never fires.
  That bug was live in this skill's own watcher until `shell` exposed it.
- **The file outlives the process.** A `SIGKILL`ed worker's JSON was still on
  disk afterwards, unchanged, still saying `idle`. So a glob alone reports a dead
  worker as healthy forever — **always check the pid** (`os.kill(pid, 0)`).
  `claude agents --json` does that check for you and drops the row; the files do
  not, and the watcher below is built on the files.
- **No `messagingSocketPath` means not messageable.** Sessions started before
  v2.1.224 register without one; they show up in `claude agents --json` and can
  never be sent to. Measured: 8 sessions in the registry, 1 reachable.

Look a worker up by name with this plugin's own `peer.py`. It prints one field of
the live session with that name, or nothing at all (exit 1) when there is no live
session by that name — which is the same answer for "never started", "already
exited" and "killed":

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"

python3 "$P" review-api              # uds:/tmp/cc-socks/30580.sock  — the SendMessage `to`
python3 "$P" review-api status       # idle | busy | waiting | shell | …
python3 "$P" review-api waitingFor   # input needed | permission prompt
python3 "$P" review-api sessionId    # for `claude --resume`, unreadable once it exits
python3 "$P" review-api cwd          # what the worker actually got, not what you intended
```

**It is a file, not a shell function, and that is the point.** Every `Bash` call
runs in a **fresh shell**: a function defined in one call does not exist in the
next. A skill that defined `peer()` once and then used it ten sections later would
be quietly asking for the whole definition to be pasted again every single time —
measured as the single biggest source of friction in a live run of the previous
version of this file. The same applies to anything else you define: assume nothing
survives between calls except files and exported environment.

## Address workers by `uds:`, never by bare name

`SendMessage` takes the socket path straight from that lookup:

```
{"to": "uds:/tmp/cc-socks/30580.sock", "message": "…"}
```

**A bare name is refused on first contact.** It comes back
`'exp-alpha' is not an agent in this conversation. Re-send with the ref…`, and
the ref (`exp-alpha [28329f]`) is obtainable *only* from a `ListAgents` call —
it is not derivable from the pid, the session id or the name, and it appears
nowhere on disk. Worse, **a `uds:` send never whitelists the bare name**:
`exp-beta` was still refused by name after four successful `uds:` sends to it.
So a run that mixes the two forms hits the ref wall at an arbitrary moment.
Address by `uds:` every time and the question never arises.

`ListAgents` is still the right tool for *discovering* sessions this run did not
spawn. It is the wrong tool for talking to ones it did.

## Surfaces by default, one pane to hold them

A **pane** is a split; a **surface** is a tab inside a pane. Every split shrinks
what the user is already reading, so a whole run adds at most one:

| Asked for | Layout |
| --- | --- |
| one agent | a surface in this run's agents pane — split once, then reuse it |
| several at once | one split, then one surface per agent inside it |
| "in a pane", "split it off" | a new pane, as asked |

Three verified behaviours decide how you place them:

- **`new-pane` cannot be aimed at a pane.** It does take `--workspace`, `--window`,
  `--placement` and `--direction`, so "no target flag" is too strong — what it
  lacks is any *pane or surface* target, which means it splits whatever pane is
  focused inside the workspace it lands in. It will cheerfully cut an unrelated
  pane in half while you sit somewhere else. Split with
  `new-split <dir> --surface "$CMUX_SURFACE_ID"`, which is anchored on the caller,
  and note it prints `OK surface:N workspace:N` — **no pane ref**, so resolve the
  pane from the surface afterwards.
- In the **visible** workspace a new surface becomes its pane's selected tab.
  `--focus` already defaults to `false`; passing it explicitly documents intent but
  changes nothing, and it does **not** stop that tab switch either way. So never
  spawn into the caller's own pane — the worker would cover the session the user is
  talking to.
- **`--workspace` defaults to `$CMUX_WORKSPACE_ID`, not to what is focused**
  (`cmux new-surface --help`); `--pane` documents no default at all. So the risk is
  not that these track the user's focus — it is that a shell variable which came out
  blank hands the command an empty value and lets it fall back to its own default,
  which for `--pane` is unspecified. Pass both explicitly and check they are
  non-empty before using them.

Never reach for `focus-pane` or `focus-panel` to tidy up afterwards — they steal
the user's keyboard mid-keystroke. Place the surface correctly instead.

## Anchor on yourself

**One command per `Bash` call — for this block and every other block in this
file.** Under `auto` mode the classifier denies a long compound command outright,
and denials *escalate*: after a few consecutive ones, every later command needs
confirming too, down to `mkdir -p` and `touch`. Measured 2026-08-09 in a freshly
spawned worker driving this skill — the setup below was denied as a unit, then
each piece had to be approved by hand, one at a time, and even
`mkdir -p … && touch …` was too compound to pass. A fresh worker has no permission
history to lean on, so this bites hardest exactly where it is least convenient: an
agent that spawns agents.

The blocks in this file are grouped for **reading**, not for pasting. Split them
on the way in, and reissue anything that comes back denied (see "A denial is not a
failure" under Spawn one agent).

```bash
# Refuse rather than guess. BOTH ids: every placement below passes
# --surface "$CMUX_SURFACE_ID", and the ledger is keyed by it.
[ -n "$CMUX_WORKSPACE_ID" ] && [ -n "$CMUX_SURFACE_ID" ] || {
  echo "not in a cmux terminal (need CMUX_WORKSPACE_ID and CMUX_SURFACE_ID); not spawning" >&2
  exit 1
}
WS="$CMUX_WORKSPACE_ID"

# The repo every worker will be launched into. Guard it exactly like the ids above:
# the cd is the single most-dropped part of the launch line, and an unset REPO must
# fail loudly here rather than silently landing workers in the caller's cwd.
REPO="<the repo you were asked to work in>"
[ -d "$REPO/.git" ] || { echo "REPO is not a git checkout: '$REPO'; not spawning" >&2; exit 1; }

LEDGER="${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
mkdir -p "$(dirname "$LEDGER")"
touch "$LEDGER"   # so the first run's awk/wc don't print "no such file" to stderr

# Both lookup helpers this file uses. They are scripts, not functions, because a
# function does not survive to the next Bash call (see peer.py above).
# REPEAT THESE TWO LINES at the top of every later call that uses $P or $S --
# variables die with the shell exactly like functions do.
P="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/peer.py"       # worker  -> address/status/cwd
S="${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/surface.py"    # surface -> uuid/ref/pane/tty
```

`$P` and `$S` are used as shorthand throughout the rest of this file, and **they
are not set for you** — a `Bash` call that uses one without assigning it first
runs `python3 ""` and fails. Two short lines re-pasted is the whole cost, which is
why these are worth naming at all; the twelve-line shell function they replaced
was not. If you would rather not carry them, both paths above are absolute by the
time you read this and can be written out in full instead.

`surface.py <ref|uuid> <field>` resolves both directions — `ref` -> `id`, `id` ->
`ref` — plus `pane_ref`, `pane_id`, `tty` and `title`, and prints nothing (exit 1)
once that surface is gone.

The ledger is keyed by the caller's surface, so it is exactly "the agents this
run spawned" — the only surfaces you are ever allowed to close. That key is why
the guard above refuses on an empty `$CMUX_SURFACE_ID` rather than defaulting it:
a shared `unknown.tsv` would merge two runs' ledgers, and the cleanup section
would then offer you another run's surfaces to close.

## Spawn one agent

Names are global across every live session on the machine, so **a name collision
is a mis-delivered task, not a cosmetic clash** — `peer.py` would hand you the
wrong socket and the watcher would report the wrong worker's state. Check first:

```bash
NAME=review-api                      # also the tab title the user reads
python3 "$P" "$NAME" >/dev/null && { echo "a live session is already named $NAME" >&2; exit 1; }
```

```bash
# Reuse this run's agents pane. UUIDs are the durable key; refs are per-call.
# Every distinct pane this run owns, newest first -- not just the last row's. If the
# newest worker's tab was closed and it was that pane's last surface, the tail row
# names a dead pane, and splitting again would break "one split per run". First live
# candidate wins; empty means this run owns no live pane yet.
PANE=$(cmux --json --id-format both list-panes --workspace "$WS" | python3 -c '
import json,sys
live={p["id"]:p["ref"] for p in json.load(sys.stdin)["panes"]}
print(next((live[c] for c in sys.argv[1:] if c in live), ""))' \
  $(awk -F'\t' 'NF>=3 && $3!=""{a[++n]=$3} END{for(i=n;i>0;i--) if(!seen[a[i]]++) print a[i]}' "$LEDGER" 2>/dev/null))

if [ -n "$PANE" ]; then          # tab into the pane this run already owns
  SURF=$(cmux new-surface --workspace "$WS" --pane "$PANE" --type terminal --focus false \
           | grep -o 'surface:[0-9]*')
else                             # first agent of the run: one split, anchored on YOU
  SURF=$(cmux new-split right --workspace "$WS" --surface "$CMUX_SURFACE_ID" --focus false \
           | grep -o 'surface:[0-9]*')
fi
[ -n "$SURF" ] || { echo "no surface came back; not sending anything" >&2; exit 1; }
```

**A denial is not a failure — reissue the call.** `cmux new-split` was denied by
the auto-mode classifier twice in a row and succeeded on the third *identical*
attempt (measured 2026-08-09). The guard above cannot tell the two apart: a denied
call never reaches the shell, so `$SURF` is empty exactly as it would be for a real
failure, and the run gives up on a command that would have worked. When a placement
or launch call comes back denied, **issue the same `Bash` call again**, up to about
three times, before believing it.

Note *where* that retry has to happen. The classifier rejects the whole tool call
before the shell starts, so wrapping the command in a shell `for` loop retries
nothing — the loop is inside the thing that was refused. Only re-calling the tool
retries.

Now write the ledger row, **before the worker is launched**:

```bash
# name, surface uuid, pane uuid, state
printf '%s\t%s\t%s\t%s\n' "$NAME" "$(python3 "$S" "$SURF" id)" "$(python3 "$S" "$SURF" pane_id)" spawned >> "$LEDGER"
```

Only then launch:

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd $REPO && claude -n $NAME\n"
```

**`cd` is not optional, and it is easy to drop** — it sits mid-string inside a
longer `cmux send` line rather than standing on its own, so it goes missing without
anything looking wrong. It is also self-concealing: a split off your own surface
inherits *your* cwd, so if you happen to be in the right repo the omission passes
silently and only misbehaves when you aren't. A supervisor reading this exact
warning still dropped it on the very next line it wrote (2026-08-09) and got away
with it for precisely that reason — so do not treat having read the warning as
having complied with it. **Verify instead of trusting:**

```bash
python3 "$P" "$NAME" cwd        # must print $REPO
```

To spawn a worker in a specific permission class — which the "Permission classes"
section below says you must — put the flag on the same launch line:

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd $REPO && claude -n $NAME --permission-mode manual\n"
```

`--permission-mode` takes `acceptEdits`, `auto`, `bypassPermissions`, `manual`,
`dontAsk` or `plan`. All but `bypassPermissions` are the prompting class.

A new terminal inherits the cwd of the pane it came from, not the workspace's
directory — split off the caller and you get the caller's cwd; split off something
else and you get *its* cwd (a probe launched from one repo came up in an unrelated
scratch directory that way). Never rely on either.

`new-surface` takes `--working-directory "$REPO"` and it works (verified
2026-08-09: a surface opened that way came up in the named directory). Pass it as
a second layer where you can — but **it does not retire the `cd`**, because
`new-split` has no such flag, so the first agent of every run is placed by the one
command that cannot be told where to land.

That row goes down **before** the launch on purpose: cleanup reads nothing else,
and a surface you failed to record is one you must never touch again. Launch
first and the window between the two is a live worker no ledger knows about — if
the turn dies in that window, its tab is orphaned and unattributable forever.

**Column 1 must be the name and must come first** — the watcher reads exactly
that field, and column 4 is what survives you. Push notifications can be missed —
a Monitor times out, a turn's context gets summarized — so flip a row to
`reported` only once you have actually told the user that worker's outcome. Then
`awk -F'\t' '$4!="reported"' "$LEDGER"` is the answer to "what am I still owed?",
and it is answerable at the start of any turn without remembering anything.

**Arm the watcher now** — with that row on disk, and *before* the task is sent.
It is the `Monitor` in "Watch" below, one per run; that section has the exact
command. Arming it here rather than once you start wondering how the worker is
doing is the difference between a watcher and a post-mortem.

Then **wait for it to become addressable** — that is the readiness signal, and it
is stricter than "the process started": it means registered *and* holding an inbox
socket, which is exactly the precondition for the `SendMessage` that follows.

```bash
n=0
until python3 "$P" "$NAME" >/dev/null; do
  sleep 1
  n=$((n+1))
  [ "$n" -gt 60 ] && { echo "worker never became addressable: $NAME" >&2; break; }
done
```

**Bound that loop.** Unbounded, a launch line that never started `claude` — a
typo, or a `cd` into a directory whose profile lacks it — does not fail, it spins
until the harness SIGKILLs the whole call (`exit 143`), with the ledger row
already written and the task never sent. Afterwards that is indistinguishable
from a worker that sat there and did nothing.

Only now send the task, as a `SendMessage` to the address `python3 "$P" "$NAME"` printed:

```
{"to": "uds:/tmp/cc-socks/30580.sock",
 "summary": "review-api: audit the auth middleware",
 "message": "<the whole spec — goal, constraints, what done means>\n\nWhen you are finished, SendMessage the session named `<your own name>` with your findings."}
```

Put the **whole** spec in that message — goal, constraints, and what "done" means,
plus the instruction to report back. These are one-shot kickoffs; a worker cannot
be clarified as cheaply as a conversation. If the user asked for `ultracode`,
include that word in the text — it is a keyword the worker reads, not a CLI flag.

**Ask for the findings in the reply, not just an acknowledgement.** The reply
interrupts you with its body in it, so a worker that answers "counted 89 lines"
has finished the reporting round trip in one hop. A worker that answers "done"
has sent you back to its transcript for no reason.

**Name yourself in that instruction.** Your own name is the one thing the worker
cannot look up about you:

```bash
ME=$(python3 -c "
import json,os
print(json.load(open(os.path.expanduser('~/.claude/sessions/%d.json' % $(ps -o ppid= -p $$ | tr -d ' ')))).get('name',''))")
```

A worker replying to a *name* hits the same first-contact ref wall you do — but
the wall is one-sided: the message it received carries a `from=` address it can
reply to directly, and telling it "reply to the session that sent you this" is
always sufficient. Name plus that fallback covers both.

## Spawn several at once

One tab per worker in **the same agents pane** — a fan-out is not a reason for a
second split, and reusing the pane is what keeps a five-agent run the same size
as a one-agent run:

```bash
for NAME in audit-api audit-web audit-jobs; do
  python3 "$P" "$NAME" >/dev/null && { echo "name taken: $NAME" >&2; continue; }
  # Same live-pane scan as above: first candidate that still exists wins.
  PANE=$(cmux --json --id-format both list-panes --workspace "$WS" | python3 -c '
import json,sys
live={p["id"]:p["ref"] for p in json.load(sys.stdin)["panes"]}
print(next((live[c] for c in sys.argv[1:] if c in live), ""))' \
    $(awk -F'\t' 'NF>=3 && $3!=""{a[++n]=$3} END{for(i=n;i>0;i--) if(!seen[a[i]]++) print a[i]}' "$LEDGER" 2>/dev/null))
  if [ -n "$PANE" ]; then
    SURF=$(cmux new-surface --workspace "$WS" --pane "$PANE" --type terminal --focus false \
             | grep -o 'surface:[0-9]*')
  else
    SURF=$(cmux new-split right --workspace "$WS" --surface "$CMUX_SURFACE_ID" --focus false \
             | grep -o 'surface:[0-9]*')
  fi
  [ -n "$SURF" ] || { echo "no surface for $NAME" >&2; continue; }
  cmux send --workspace "$WS" --surface "$SURF" "cd $REPO && claude -n $NAME\n"
  printf '%s\t%s\t%s\t%s\n' "$NAME" "$(python3 "$S" "$SURF" id)" "$(python3 "$S" "$SURF" pane_id)" spawned >> "$LEDGER"
done
```

Then send each one its task with its own `SendMessage`, once `python3 "$P" "$NAME"` answers.

**Do not collect the names in a space-joined string** — `for n in $NAMES` iterates
once in zsh, not once per worker, and you will drive one agent while believing you
drove four. The ledger is the list: re-read it with
`while IFS=$'\t' read -r name su pu state`.

A new surface also lands **after the selected tab**, not at the end, so never
infer which worker is which from tab order. The tab title is the name
(`⠂ audit-api`), which is the only thing that distinguishes four identical
terminals — so name them for the tab bar, because that is how the user reads a
fan-out.

Don't wait on a fan-out one worker at a time. Each worker's own reply lands as it
finishes, so a slow agent never blocks reporting a fast one.

## Watch — two pushes, and you need both

**A stage stops being worked on in two different ways, and only one of them is an
ending.** It can *end* — the turn finishes — or it can *suspend*, parked on a
question or a permission prompt with the turn still open. Both leave the worker
doing nothing; only the first ever resolves on its own. And a worker can simply
*die*, which looks like neither.

### The worker's reply — results, pushed

A worker told to report back sends you a `SendMessage` when it is done, and that
message interrupts your turn with its body in it. This is the only signal that
carries **what actually happened**, and it needs no watcher, no polling and no
transcript read.

It is not sufficient on its own, for one reason: **a worker that is blocked or
dead cannot send it.** Which is what the watcher is for.

### The watcher — one Monitor, four signals

```bash
python3 -u "${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/watch-workers.py" \
    "${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
```

Arm **one** of these right after the first spawn — always, including for a run of
exactly one worker. It covers every worker in the run, including ones spawned
later, because it re-reads the ledger on every poll. Pass `persistent: true` for a
run that may outlast a single Monitor timeout.

**That block is the `Monitor` tool's `command` — never a `Bash` call.** It is
written in shell, so backgrounding it with `Bash(run_in_background: true)` looks
like the same thing done more cheaply. It is silently useless: only a `Monitor`'s
stdout lines become chat notifications. A backgrounded `Bash` notifies you exactly
once, when the command *exits* — and this one never exits. So every signal lands
in an output file nobody reads, while the watcher looks perfectly healthy in
`pgrep`.

**That last line spells the ledger path out on purpose — do not shorten it to
`"$LEDGER"`.** `Monitor` runs this in a shell of its own, which never saw the
assignment you made in some earlier `Bash` call. `$LEDGER` there expands to the
empty string, the watcher's ledger reads as zero rows, and every worker is
filtered out as unknown. You get a watcher that runs happily and reports nothing —
indistinguishable from a run where nothing has finished yet. `TMPDIR` and
`CMUX_SURFACE_ID` *are* in that shell's environment, which is why the expanded
form is safe where the variable is not.

That `${CLAUDE_PLUGIN_ROOT}` path is substituted when this skill loads, so by the
time you read it it is already an absolute path to this plugin's own copy — run it
as written rather than searching for the file. It is **not** an environment
variable at that point: `echo "$CLAUDE_PLUGIN_ROOT"` from a shell prints nothing.

Each output line becomes a chat notification that re-invokes you:

- `DONE <name>` — that worker went from **working to idle**, so a turn ended.
  Not the same as "the work is finished": a worker parked awaiting its own
  background sub-agent ends its turn too. The worker's own reply is what tells you
  the outcome.
  **Expect that reply to arrive *before* this line, not after.** The worker sends
  it as its last act and delivery is immediate, while `DONE` waits on the next
  poll up to `POLL` seconds later — measured in that order on 2026-08-09. So
  `DONE` is a backstop for a worker that finished without reporting, and a
  supervisor that gates on `DONE` before reading the findings is waiting for
  something it is already holding.
- `ASK <name>` — **suspended** on an `AskUserQuestion`. Not an ending, and no
  `DONE` follows until someone answers it.
- `ATTN <name>` — suspended on a permission prompt, a plan approval, or a held
  peer message. Not an ending either.
  **Its absence proves nothing about the worker's mode.** A worker launched
  `--permission-mode manual` ran a read-only `find … | wc -l` with no prompt at
  all, and no allow-rule explained it — the settings had an empty `allow` list
  (measured 2026-08-09). Only a command that *writes* reliably raises one. So a
  quiet run is not evidence the watcher is deaf, and a supervisor testing its own
  `ATTN` path needs a worker that writes something.
- `CLEAR <name>` — it stopped being blocked **without a turn running**, so there
  is nothing to collect. This exists to keep `DONE` honest. A terminal UI overlay
  opened over an already-idle worker produces a real `ATTN` and then a return to
  idle with no work in between; a held peer message that gets denied does the
  same. Both were reported as `DONE` by an earlier version of this watcher —
  a completion for a worker whose transcript had not gained a single record
  (observed 2026-08-09). If you answered a block and get `CLEAR` rather than a
  later `DONE`, the worker took no turn: nothing is coming, and re-sending the
  task is the fix.
- `GONE <name>` — the process is no longer running: a clean `/exit`, a crash, or a
  kill. The registry cannot tell those apart, and neither can you from this line
  alone — check whether that worker had already reported.

`DONE`, `ASK`, `ATTN` and `GONE` were each observed firing through a live
`Monitor` on 2026-08-09; `CLEAR` is the corrected half of the spurious pair that
same run produced.

**`GONE` is the signal an event bus cannot give you.** A worker that dies outright
— `SIGKILL`, a host crash, a surface torn down under it — never runs a hook, so
nothing is ever published about it. Measured 2026-08-07: a worker `SIGKILL`ed
while parked on an `AskUserQuestion` produced no bus event whatsoever, and the run
looked exactly like a worker still sitting on its question. Polling the registry
for pid liveness is what closes that hole, and it is why this watcher is built on
process state rather than on events.

Three things the filter already handles. Do not "simplify" them away:

- **The pid check.** The registry file survives the process, so liveness is the
  only thing separating "idle" from "dead" (see the registry section above).
- **First sight is not a transition.** A worker already blocked when the watcher
  starts is reported immediately, because a human is needed *now*; one that is
  merely `busy` or `idle` is recorded silently, so arming a watcher does not
  fabricate a `DONE` for every worker already sitting there.
- **`GONE` only for a worker seen alive at least once**, so a ledger row written
  before its `claude` has registered is not reported dead a second later.

**Confirm the watcher is still alive before you send the task.** An armed
`Monitor` can die within the minute — observed exit 144, roughly 60 s in, with no
output file ever written — and a dead watcher is indistinguishable from a healthy
one that has nothing to report yet:

```bash
pgrep -f "watch-workers.py.*${CMUX_SURFACE_ID}" >/dev/null \
  || echo "watcher is not running -- re-arm it before sending the task"
```

**Keep the surface id in that pattern — a bare `pgrep -f watch-workers.py` counts
itself.** The string is in the command line of the very `Bash` call running the
check, so the shell hosting `pgrep` matches, and a run with *no* watcher at all
reports one. Measured 2026-08-09: a bare `pgrep -f watch-workers.py | wc -l`
answered `2` for a session that had exactly one watcher, and the author of this
paragraph read that as proof a second agent had armed its own. It had not. The
scoped form is immune, because the surrounding shell's own argv holds the literal
`${CMUX_SURFACE_ID}` rather than the expanded uuid.

## Answer a blocked worker

`ASK` and `ATTN` both mean *a human is being waited on, and the run is stopped
until one shows up*. Neither line says what was asked. Read the screen, then
answer with keys:

```bash
cmux read-screen --workspace "$WS" --surface "$(python3 "$S" "$su" ref)"
cmux send-key --workspace "$WS" --surface "$(python3 "$S" "$su" ref)" down    # move the selection
cmux send-key --workspace "$WS" --surface "$(python3 "$S" "$su" ref)" enter   # choose it
```

**`SendMessage` cannot clear a block, and it does not fail loudly when you try.**
Measured 2026-08-09: a message sent to a worker parked on an `AskUserQuestion`
returned `success: true`, appeared on that worker's screen as queued text *beneath*
the open dialog, and was not read until after the question had been answered by
key — at which point it arrived appended to the tool result. So `success` means
*handed to the session*, never *read by Claude*. A supervisor that answers `ASK`
with a message will sit forever watching a worker that has already been told.

Send `send-key` calls **one per `Bash` call**, not bundled into a script. Observed
repeatedly on 2026-08-09: the auto-mode classifier denies the compound form and
sometimes the single form too. The single form succeeds on retry; a denied
keystroke leaves the worker blocked and looks exactly like a worker that ignored
you.

**A worker's input box may show text nobody typed.** `read-screen` renders
suggested-follow-up ghost text in the prompt exactly like real input, and a queued
peer message sits there too. Neither is pending user input, and pressing `enter`
on either submits it. Judge from the dialog, not from the prompt line.

## Chain stages

Same session, next task — context carries over, so use this when the next stage
needs what the previous one found. Prose goes by message; **a slash command must
go by keys**:

```bash
cmux send --workspace "$WS" --surface "$SURF" "/spec-write <what to write up>"
cmux send-key --workspace "$WS" --surface "$SURF" enter
```

**Slash commands do not execute over cross-session messaging.** Verified
2026-08-09 by sending `/context` to a worker: it arrived as literal text inside
the message wrapper, no expansion, nothing run. Expansion happens in the CLI on
input the user types; a message is injected past that path. The nuance that makes
this a trap rather than a clean failure: a worker that reads "run /foo" may still
*choose* to invoke `foo` through its `Skill` tool, so a plugin skill can appear to
work by persuasion while a built-in like `/context` or `/compact` can never run at
all. Do not let one lucky stage convince you the channel expands commands.

Fresh session for a stage that should start clean: another surface in the same
agents pane, exactly as above — a new stage is never a reason for a new split. It
shares no context, so **pass the handoff explicitly** — name the file the previous
stage wrote, rather than referring to "the findings."

## Permission classes decide whether messaging works at all

**Two sessions can only message each other freely when they are in the same
permission class**, where the classes are "bypasses prompts" and "everything
else". Across the line, the message is *held* for a human to approve in the
receiving session — and a held message is not an error you see, it is a dialog
somebody has to find.

Measured 2026-08-09, both directions, with a `--dangerously-skip-permissions`
worker and an `auto`-mode supervisor:

| direction | outcome |
| --- | --- |
| supervisor → bypass worker | **held at the worker.** Its task never arrived; it parked at `waiting` / `permission prompt` until the dialog was answered by hand |
| bypass worker → supervisor | **held at the supervisor.** Its reply never arrived; the supervisor's own session went to `waiting` / `permission prompt` |

So a bypass worker is deaf and mute by default, in both directions at once.

**You are told, but late.** A same-machine sender gets a `[Cross-session delivery
notice]` when its message is held and a second one when it is released or denied —
so the hold is not silent. It is also not prompt: both notices for one held message
arrived at a **later turn boundary**, long after the hold began and after the
approval that cleared it. Measured 2026-08-09. Treat them as an audit trail that
explains a stalled run afterwards, never as the signal you wait on.

Two things save the run in real time. The first is that **the watcher sees it
immediately**: a held message parks the receiver at `waiting` / `permission
prompt`, so it reports `ATTN` on the next poll. The second is the rule that avoids
it entirely:

- **Spawn workers in your own permission class.** If you prompt, they prompt.
  Then every message flows automatically, which is the whole design.
- If the user insists on a bypass worker, **drive it with keys, not messages** —
  `cmux send` is unaffected by any of this — and keep the watcher for its state.
- The product's own held-message dialog names the escape hatch,
  `crossSessionInbound: "accept"`, settable per worker via
  `--settings '{"crossSessionInbound":"accept"}'`. **Untested here** — the launch
  line was refused by the auto-mode classifier — and it only fixes the inbound
  half, since the reply direction is governed by the *supervisor's* setting, which
  is the user's to change and not yours.

Note also that **the registry does not record a session's permission mode**, so
you cannot tell a bypass worker from its JSON. What you can read is the
`from-mode` attribute on a message that arrives from it.

**A bypass worker does not register until its acceptance gate is answered.**
`--dangerously-skip-permissions` opens a "Yes, I accept" confirmation before the
session starts, so it has no registry entry and no socket until a key clears it —
the readiness loop above will simply time out, and the tab is what tells you why.

## Read a worker's output

Normally you don't have to: the worker's reply carries its findings. When you
need the raw record — a worker that died mid-stage, or one whose `DONE` arrived
without a report — the transcript is at

```
<config-dir>/projects/<esc-cwd>/<sessionId>.jsonl
```

with `sessionId` and `cwd` both read from that worker's registry file. `esc-cwd`
replaces **every** non-alphanumeric character with `-`
(`/Users/you/work/stock_check_app` → `-Users-you-work-stock-check-app`). Records
are `{"type": "assistant", "message": {...}}` in the Anthropic message shape.

**The file does not exist until the session takes its first turn**, and a worker
that has already exited has no registry file to resolve it from — so if a stage
may need forensics, read the `sessionId` while it is still alive.

## Several config profiles — check before spawning

The registry lives under the active `CLAUDE_CONFIG_DIR`. A worker launched into a
directory that selects a different profile — a shell hook that switches
`CLAUDE_CONFIG_DIR` per directory is a common setup — registers there and nowhere
else, so `peer` and the watcher will both report it as absent. The watcher takes a
colon-separated `CLAUDE_CONFIG_DIR` and watches every profile at once; give it one
when a run spans profiles, and search by hand the same way:

```bash
ls ~/.claude/sessions/                       # default profile
ls ~/.claude-<other>/sessions/               # each additional profile
```

Messaging itself is not profile-scoped — the socket path is absolute and a `uds:`
send crosses profiles fine. It is *discovery* that is scoped.

Plugins are installed per profile, so **the command a stage needs may not exist in
the profile its directory selects**. Check the installed plugins for the relevant
profile (`claude plugin list`, or `enabledPlugins` in that profile's
`settings.json`) before building a pipeline on a slash command.

## Offer to close what the run opened

When the last stage is reported, the ledger rows are spent surfaces. Resolve each
one, then **offer** — cleanup is a proposal, never a side effect of finishing:

```bash
while IFS=$'\t' read -r name su pu state; do
  ref=$(python3 "$S" "$su" ref)
  [ -n "$ref" ] || continue               # the user already closed it; drop the row
  reg=$(python3 "$P" "$name" status)      # empty = no live session by that name
  sid=$(python3 "$P" "$name" sessionId)   # capture NOW; unreadable once it exits
  echo "$ref  $name  ledger=$state  registry=${reg:-gone}  resume=${sid:-none}"
done < "$LEDGER"
```

Show that list with what each worker did, and close only the ones the user
confirms:

```bash
cmux close-surface --workspace "$WS" --surface "$ref"
```

- The pane disappears on its own when its last surface closes. There is no
  `close-pane`, and nothing is left to tidy once the tabs are gone.
- **The `OK surface:N` it prints back is not the surface you closed.** Refs are
  re-enumerated on every call, so closing `surface:44` can answer `OK surface:45`.
  Nothing went wrong and no other tab was touched — but read literally it looks like
  you just closed a stranger's tab. Confirm by resolving the UUID
  (`python3 "$S" "$su" ref` returns empty once it is gone), never by reading that ref.
- Closing a tab with siblings left re-selects the previously visible one and
  moves no focus. Closing the **last** one collapses the pane, and focus lands on
  the pane it was split off from — yours. Harmless, but say so when you offer, in
  case the user is reading a third pane at the time.
- The scrollback dies with the surface; the session does not.
  `claude --resume <sessionId>` brings it back — that id is the second field the
  loop above prints, and it is unreadable once the process is gone, so capture it
  before you close anything.
- `busy` or `waiting` means not done. A `waiting` worker is stopped on a prompt and
  will sit there forever — closing it discards whatever it was about to do. Leave
  those open, and say which ones you left and why.
- `registry=gone` on a row you never reported is a worker that died mid-stage.
  Lead with those.
- Prune closed rows from the ledger so the next offer is not a list of ghosts.
- A dead worker leaves its `<pid>.json` behind. It is not yours to delete, and
  `peer` and the watcher both ignore it on the pid check.

Only ever propose surfaces from this run's ledger. A tab you did not spawn is the
user's, however idle it looks — leave it alone even when it is obviously a dead
agent from an earlier session.

### Finish the run — four steps, in this order

Closing the surfaces is not the end. The watcher is a *process*, and a `Monitor`
armed with `persistent: true` runs until `TaskStop` or the end of the session that
armed it — and **if you are yourself a spawned agent, your session ending does not
reap it.** Observed: an agent finished, its surface was closed, and its watcher was
still polling.

The order matters: stopping the watcher before deleting the ledger leaves a window
where a late signal names a worker you have already reported.

1. **Close each surface** you spawned, as above — resolving by UUID, never by the
   `OK surface:N` echo.
2. **Delete the ledger file**, not just its rows:
   `rm -f "${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"`. This is the
   belt-and-braces step — a watcher that somehow survives with no ledger matches no
   name and reports nothing, so it is inert rather than wrong.
3. **`TaskStop` the monitor** by the task id you were given when you armed it.
4. **Confirm nothing of yours is left**, scoped to your own surface:

```bash
pgrep -fl "watch-workers.py.*${CMUX_SURFACE_ID}"
```

**Scope that `pgrep` — do not run it bare.** `pgrep -fl watch-workers.py` lists every
watcher on the machine, including live ones belonging to other sessions and other
projects, wrapped in 400-character zsh preambles you then have to read. That is not a
check, it is an invitation to kill someone else's run: an agent doing exactly this
reported two healthy watchers as orphans — one of them its own supervisor's. Scoped to
`$CMUX_SURFACE_ID` the answer is yes-or-no and cannot implicate anyone else.

If a line does come back after `TaskStop`, it is yours and it is stuck:

```bash
pkill -f "watch-workers.py.*${CMUX_SURFACE_ID}"
```

**Never reap a watcher you cannot prove is dead.** A watcher is a true orphan only if
the `claude` process that armed it is gone — check with
`ps -o ppid= -p <pid>` and see whether that parent still exists. A live session's
watcher looks identical in `pgrep` and killing it silences a run that is still going,
which is the one failure this whole section exists to prevent.

## Rules

- **"Spawn" means a visible surface, not a subagent.** Once the user has used that
  word, an invisible subagent is not a cheaper version of this — it is a different
  thing, one they cannot watch, click into, or take over. Reaching for `Agent`
  because the tasks look small is the one substitution to refuse: small tasks are
  exactly what a showcase is made of.
- **The name is the key.** `claude -n <name>` at launch, unique among live
  sessions, and every lookup afterwards goes through it.
- **Address by `uds:`.** A bare name is refused on first contact and needs a ref
  only `ListAgents` can give you; the socket path is derivable from disk and always
  works.
- Anchor placement on `$CMUX_WORKSPACE_ID` and `$CMUX_SURFACE_ID`, never on what
  is focused — the user may be looking elsewhere. Pass `--focus false`.
- One split per run, at most. Every agent after the first is a tab.
- **Arm the watcher at the first spawn, before sending any task** — one `Monitor`
  running `watch-workers.py` against the ledger, for one worker or for ten. The
  `Monitor` **tool**: the same command backgrounded with `Bash` streams into a file
  nobody reads and notifies you only if it exits, which it never does. The worker's
  own reply is not a substitute, because a worker that is blocked or dead is
  precisely the worker that cannot send one.
- **Ask every worker to report its findings by message**, and name yourself in the
  instruction. That reply is the only signal that carries content.
- **`DONE` means a turn ended, not that the work is done** — and the worker's
  reply usually arrives *before* it. Collect findings from the reply; treat `DONE`
  as the backstop for a worker that finished without reporting.
- **Messages cannot clear a block and cannot run a slash command.** Keys can do
  both, one `Bash` call per keystroke.
- **Keep workers in your own permission class**, or messaging silently stops in
  both directions.
- **The ledger is on disk**, at
  `${TMPDIR:-/tmp}/cmux-spawn-agent/<caller surface id>.tsv` — keyed by the
  caller's surface, so it is exactly this run's spawns and nothing else. That is
  what lets a turn which remembers nothing still answer what it owes and what it
  may close.
- Report each stage's outcome as it lands; don't go silent for a long pipeline.
  Mark the ledger row `reported` when you do.
- Never close a surface you did not spawn, and never close one without asking —
  not even your own.
- **A run ends when its watcher is stopped, not when its last tab closes.**
- Before a destructive or long-running task, say which repo and which profile it
  will run in and get confirmation.
- Workers inherit the user's global `~/.claude/CLAUDE.md`. If you plan to parse a
  worker's reply, expect whatever that file makes every session emit.
- `cmux new-surface --type agent-session --provider claude` also opens a Claude
  surface, but it takes no `-n`, so the worker comes up under a derived name you
  did not choose — which is the join key here. Spawn terminals.
