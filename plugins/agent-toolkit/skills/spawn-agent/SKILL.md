---
name: spawn-agent
description: Spawn Claude Code agents into visible terminal surfaces — cmux surfaces or herdr panes — and drive multi-stage pipelines across them. The word "spawn" is on its own a sufficient trigger — "spawn an agent", "spawn 3 agents" — with no mention of a terminal, tab or pane required. Also use when the user asks to run something "in another agent", "in a new Claude Code", "in a pane", to hand work from one agent to the next ("run X to the end, then /y, then in a new session run /z"), or to run several agents in parallel and report as each finishes. Prefer this over the built-in subagent/Task tool for all of those — a subagent is invisible and cannot be watched, clicked into, or taken over, which is the whole point of spawning one.
---

# Spawn agents into visible terminal surfaces

Run work in separate Claude Code sessions that live in visible places the user can
watch and take over by clicking.

**The join key is the name you give at launch** (`claude -n <name>`). One string is
the tab title, the peer-registry key and the message address at once. Nothing has
to be pre-assigned — no `--session-id`, no `uuidgen`.

**This file contains no terminal commands, and that is deliberate.** Everything
below is true of Claude Code regardless of which terminal you are in. The commands
that place, launch, read, key and close a worker live in one host file, and you
cannot spawn anything without it. Read it now.

## 0. Which host are you in — decide this before anything else

Two multiplexers are supported and they can be nested. Decide by **precedence, not
by presence**, because the loser's environment is still there and still looks valid:

| `HERDR_ENV` | `CMUX_SURFACE_ID` | host | read next |
| --- | --- | --- | --- |
| `1` | *(don't care)* | **herdr** | `hosts/herdr.md` |
| unset | non-empty | **cmux** | `hosts/cmux.md` |
| unset | empty | **neither** | stop — refuse to spawn |

```bash
if [ "${HERDR_ENV:-}" = 1 ]; then echo herdr
elif [ -n "${CMUX_SURFACE_ID:-}" ]; then echo cmux
else echo "not in a supported terminal (need HERDR_ENV=1 or CMUX_SURFACE_ID); not spawning" >&2; fi
```

Then read the matching file with `Read`:

```
${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/herdr.md
${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux.md
```

Those paths are already absolute by the time you read this — the token is
substituted into this text when the skill loads. It is **not** an environment
variable, so `echo "$CLAUDE_PLUGIN_ROOT"` from a `Bash` call prints nothing.

**The "don't care" in row one is load-bearing, and it is the reason this is a
precedence table rather than a pair of checks.** herdr is a server/client
architecture, so every herdr pane inherits the environment of whatever started the
herdr *server*. Measured 2026-08-12, in a herdr pane whose server had been launched
from a cmux surface:

```
HERDR_ENV=1   HERDR_PANE_ID=w9:p2
CMUX_SURFACE_ID=7EF9AE59-F515-4A2A-BA0A-77DE93F2E39C   ← the herdr TUI's own surface
```

That id is not stale and not dead. It is a **live, valid** cmux surface — the one
displaying the herdr window — and **every herdr pane on the machine carries the same
one**. A worker that trusted it would aim every placement command at the herdr
window and key its ledger to a file shared by every herdr pane there is, which is
precisely the collision the per-caller key exists to prevent. Every command would
return `OK`. Confirmed twice, once by dumping the pane's environment and once from
inside a spawned worker reporting its own view.

So: check herdr first, and when it answers, do not look at `CMUX_*` again.

## Two channels reach a worker, and they are not interchangeable

| | `SendMessage` (cross-session messaging) | the host's keystroke channel |
| --- | --- | --- |
| what it is | a message from a peer session | keystrokes, as if the user typed them |
| carries | prose only | anything, including slash commands |
| quoting | none — it is a tool argument | shell- or argv-quoted through the host |
| the worker replies | yes, and the reply interrupts you with the result in it | no |
| reaches a **blocked** worker | no — it queues until the block clears | yes, and it is the only thing that clears one |

Default to `SendMessage` for tasks and prose. Use keys for slash commands, for
answering a prompt, and whenever the worker must act on the user's own authority.

| Need | Source |
| --- | --- |
| which host you are in | the table in §0 |
| where you are, and where to put a worker | the host file |
| a worker's pid, cwd, session id, status, **message address** | `<config-dir>/sessions/<pid>.json`, matched on `name` |
| finished / blocked / died, **pushed to you** | one `Monitor` running `watch-workers.py` |
| a stage's actual findings | the worker's own reply message |
| send a task | `SendMessage` to `uds:<socket>` |
| answer a prompt | the host's keystroke command |

## The peer registry is a directory of JSON files

Every session with messaging writes `<config-dir>/sessions/<pid>.json` and
rewrites it whenever its status changes. That file is the whole coordination
substrate — readable from `Bash`, no event bus, **no dependency on any terminal**:

```json
{"pid":30580,"sessionId":"8669cd04-…","cwd":"/Users/ns/workspace/agent-toolkit",
 "messagingSocketPath":"/tmp/cc-socks/30580.sock","name":"exp-alpha",
 "status":"waiting","waitingFor":"input needed"}
```

Verified 2026-08-12 that this is genuinely host-independent — the same unmodified
`peer.py` resolved a worker's address, status, cwd and sessionId for a worker
running in a herdr pane exactly as it does for one in a cmux surface.

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
P="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/peer.py"

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
measured as the single biggest source of friction in a live run of an earlier
version of this file. The same applies to anything else you define: assume nothing
survives between calls except files and exported environment.

## Address workers by `uds:`, never by bare name

`SendMessage` takes the socket path straight from that lookup:

```
{"to": "uds:/tmp/cc-socks/30580.sock", "message": "…"}
```

**A bare name is refused on first contact.** It comes back
`'exp-alpha' is not an agent in this conversation. Re-send with the ref…`, and
the refusal hands you the ref (`exp-alpha [28329f]`) inline, every time — that
and a `ListAgents` call are the two sources. It is not derivable from the pid,
the session id or the name, and it appears nowhere on disk. Worse, **a `uds:`
send never whitelists the bare name**:
`exp-beta` was still refused by name after four successful `uds:` sends to it.
So a run that mixes the two forms hits the ref wall at an arbitrary moment.
Address by `uds:` every time and the question never arises.

`ListAgents` is still the right tool for *discovering* sessions this run did not
spawn. It is the wrong tool for talking to ones it did.

## Anchor on yourself

**One command per `Bash` call — for this block and every other block in this
file, and in the host file too.** Under `auto` mode the classifier denies a long
compound command outright, and denials *escalate*: after a few consecutive ones,
every later command needs confirming too, down to `mkdir -p` and `touch`. Measured
2026-08-09 in a freshly spawned worker driving this skill — the setup below was
denied as a unit, then each piece had to be approved by hand, one at a time, and
even `mkdir -p … && touch …` was too compound to pass. A fresh worker has no
permission history to lean on, so this bites hardest exactly where it is least
convenient: an agent that spawns agents.

The blocks in this file are grouped for **reading**, not for pasting. Split them
on the way in, and reissue anything that comes back denied.

**A denial is not a failure — reissue the call.** A placement command was denied by
the auto-mode classifier twice in a row and succeeded on the third *identical*
attempt (measured 2026-08-09). Your guards cannot tell the two apart: a denied call
never reaches the shell, so the variable you were capturing is empty exactly as it
would be for a real failure, and the run gives up on a command that would have
worked. When a placement or launch call comes back denied, **issue the same `Bash`
call again**, up to about three times, before believing it.

Note *where* that retry has to happen. The classifier rejects the whole tool call
before the shell starts, so wrapping the command in a shell `for` loop retries
nothing — the loop is inside the thing that was refused. Only re-calling the tool
retries.

### The caller's slot

A **slot** is the visible container a session occupies — a cmux surface, a herdr
pane. `$CALLER_SLOT` is yours, and the host file's first section is the one line
that binds it. Whatever a host binds to it must have four properties, because each
one is load-bearing somewhere below:

1. **Durable** — it outlives any one `claude` process, so quitting and relaunching
   in the same place reopens the same ledger.
2. **Unique per caller** — no two live sessions may share it, or their ledgers
   merge and the cleanup section offers you another run's slots to close. (This is
   the property inherited `CMUX_*` violates inside herdr, and the whole reason §0
   is a precedence table.)
3. **Filename-safe** — it is a path component. A host whose ids contain `:` or `/`
   binds a sanitised form.
4. **Non-empty, or you refuse.** Not defaulted — refused. A blank slot id would
   send every run to one shared ledger.

### The setup block

```bash
# Bind the slot HERE, in this call, with your host's line from the host file --
# one of these two, not both. A Bash call's shell state does not outlive the call,
# so the binding you made while reading the host file is already gone; measured
# 2026-08-12, not even `export` crosses a Bash call. $CMUX_SURFACE_ID and
# $HERDR_PANE_ID do survive, because they are in the process environment.
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr

# Refuse rather than guess.
[ -n "$CALLER_SLOT" ] || {
  echo "no caller slot; not spawning" >&2
  exit 1
}

# The repo every worker will be launched into. Guard it exactly like the slot
# above: the working directory is the single most-dropped part of the launch, and
# an unset REPO must fail loudly here rather than silently landing workers
# somewhere neither you nor the user chose.
REPO="<the repo you were asked to work in>"
[ -d "$REPO/.git" ] || { echo "REPO is not a git checkout: '$REPO'; not spawning" >&2; exit 1; }

LEDGER="${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
mkdir -p "$(dirname "$LEDGER")"
touch "$LEDGER"   # so the first run's awk/wc don't print "no such file" to stderr

# Refuse a ledger that is not this format. touch neither truncates nor inspects.
awk -F'\t' 'NF && NF!=4 {print FILENAME": "NR" columns="NF; bad=1} END{exit bad}' "$LEDGER" \
  || { echo "stale/foreign ledger at $LEDGER -- rm it or move it aside before spawning" >&2; exit 1; }

# The lookup helper this file uses. It is a script, not a function, because a
# function does not survive to the next Bash call (see peer.py above).
# REPEAT THIS LINE at the top of every later call that uses $P -- variables die
# with the shell exactly like functions do.
P="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/peer.py"
```

`$P` is used as shorthand throughout the rest of this file, and **it is not set for
you** — a `Bash` call that uses it without assigning it first runs `python3 ""` and
fails. One short line re-pasted is the whole cost, which is why it is worth naming
at all; the twelve-line shell function it replaced was not. If you would rather not
carry it, the path above is absolute by the time you read this and can be written
out in full instead.

### The ledger

Four tab-separated columns, and the core only ever interprets two of them:

```
name <tab> loc1 <tab> loc2 <tab> state
```

- **column 1 is the name** — the watcher reads exactly this field.
- **columns 2 and 3 are host locators.** The host file says what they hold and how
  to resolve them. Nothing in this file interprets them; it only requires that
  resolving column 2 answers "does this slot still exist" and that both are
  non-empty before a row is written.
- **column 4 is the state**, and it is what survives you. Push notifications can be
  missed — a Monitor times out, a turn's context gets summarized — so flip a row to
  `reported` only once you have actually told the user that worker's outcome. Then
  `awk -F'\t' -v c=4 '$c!="reported"' "$LEDGER"` is the answer to "what am I still
  owed?", and it is answerable at the start of any turn without remembering anything.

  **That `-v c=4` is not a style choice — an awk field reference written as a dollar
  sign followed by the digit 4 does not survive being read.** A skill invoked with
  arguments has every bare dollar-plus-digit in its text replaced by one of those
  arguments, zero-indexed, before you ever see it. Measured 2026-08-12 with a probe
  skill invoked as `/probe ZULU YANKEE XRAY WHISKEY VICTOR`, where the literal field
  reference for column 4 was served as `VICTOR` — leaving valid awk, the wrong query,
  and no error anywhere. A dollar sign followed by a *letter* is untouched, which is
  why `$c`, `$i` and `$LEDGER` are all safe. This is why you will not see a bare
  dollar-digit anywhere in this skill, including in prose warning you about it.

The ledger is keyed by the caller's slot, so it is exactly "the agents this run
spawned" — the only slots you are ever allowed to close.

**That same key is why the format check is not paranoia.** A slot id outlives any
one `claude` process, so quitting and relaunching in the same place reopens the
*same* ledger file, rows and all — including rows written by an older, differently
shaped version of this skill. Five such five-column ledgers were on this machine
when the check was written. The watcher reads column 1 as a name, so on one of them
it watches session uuids and matches nothing. It does now say so — one `WARN` line
about 30 s in, once, and then never again — but a line you have to notice and act on
is a weaker guarantee than a ledger that was right to begin with, and for those first
30 s it is still indistinguishable from a healthy watcher whose workers are going.

## Spawn one agent

Names are global across every live session on the machine, so **a name collision
is a mis-delivered task, not a cosmetic clash** — `peer.py` would hand you the
wrong socket and the watcher would report the wrong worker's state. Check first:

```bash
NAME=review-api                      # also the tab title the user reads
python3 "$P" "$NAME" name >/dev/null && { echo "a live session is already named $NAME" >&2; exit 1; }
```

**Ask for `name`, not for the default address field.** `peer.py` exits 1 both for
"no session by that name" and for "that session has no `messagingSocketPath`", and
the second case is the common one: measured on this machine, 7 live sessions, all 7
named, **6 with no socket** (all on a CLI older than v2.1.224). Checked by address,
the guard calls 6 of the 7 names in use free — and hands the collision it was built
to catch straight through. `name` is present for every registered session.

Keep the name within `[a-z][a-z0-9_-]{0,31}`. That is herdr's constraint on agent
names, not Claude Code's, but a name that satisfies it works on every host and can
be used unchanged as both the herdr agent name and the `claude -n` name — which is
what keeps one string as the single join key.

Then, **in this order**:

1. **Place the slot** — the host file's placement section. It hands back the two
   locators the ledger row needs.
2. **Write the ledger row, before the worker is launched.**
3. **Arm the watcher** (see "Watch" below), with that row on disk and *before* the
   task is sent.
4. **Launch** `claude -n $NAME` in `$REPO` — the host file's launch section.
5. **Wait for it to become addressable.**
6. **Send the task** by `SendMessage`.

```bash
# 2. Resolve, guard, then write -- never inline the resolution into the printf.
L1="<host locator 1>"
L2="<host locator 2>"
[ -n "$L1" ] && [ -n "$L2" ] || { echo "could not resolve the new slot; not launching" >&2; exit 1; }
printf '%s\t%s\t%s\t%s\n' "$NAME" "$L1" "$L2" spawned >> "$LEDGER"
```

**Resolve, guard, then write.** A host resolver prints nothing and exits non-zero
when a slot will not resolve, and command substitution throws that status away, so
the inline form writes the row anyway with two empty fields. It is worse than a
blank-looking row: tab is IFS whitespace, so the cleanup loop's
`IFS=$'\t' read -r name l1 l2 state` collapses the adjacent tabs and shifts every
column left — `l1` receives the literal `spawned`, the row then fails to resolve,
and it is dropped as "already closed". A real open slot that can never be offered
for closing. (Reproduced identically in bash; this is POSIX field splitting, not a
zsh quirk.) A slot you cannot record is a slot you must not launch into — hence the
`exit 1` rather than a warning.

That row goes down **before** the launch on purpose: cleanup reads nothing else,
and a slot you failed to record is one you must never touch again. Launch first and
the window between the two is a live worker no ledger knows about — if the turn dies
in that window, its slot is orphaned and unattributable forever.

The ordering does not close the window entirely, and it is not meant to: the slot is
created *before* the row is written, so there is always an interval in which an
unrecorded slot is open. What the ordering buys is what is inside it — an empty
terminal rather than a running agent. Both leak a slot; only one leaks a slot that
is doing work you can no longer see, address or stop.

Arming the watcher at step 3 rather than once you start wondering how the worker is
doing is the difference between a watcher and a post-mortem.

### Readiness is registration, not "the process started"

```bash
n=0
until python3 "$P" "$NAME" >/dev/null; do
  sleep 1
  n=$((n+1))
  [ "$n" -gt 60 ] && { echo "$NAME never became addressable -- read its screen" >&2; break; }
done
```

That is the readiness signal, and it is stricter than "the process started": it
means registered *and* holding an inbox socket, which is exactly the precondition
for the `SendMessage` that follows.

**This loop asks for the default address field on purpose — do not "unify" it with
the collision check above.** Readiness is "registered *and* holding a socket";
collision is "the name exists at all". Two opposite predicates that happen to share
one script, and a check written to answer both would be wrong in one of them.

**Bound that loop.** Unbounded, a launch that never started `claude` does not fail,
it spins until the harness SIGKILLs the whole call (`exit 143`), with the ledger row
already written and the task never sent. Afterwards that is indistinguishable from a
worker that sat there and did nothing.

**Run this loop even when the host told you the worker is ready, and especially
then.** One host does claim it: herdr's `agent start` returns
`interactive_ready: true`, exit 0, in about three seconds — and it returns that
while the worker is parked on a pre-registration gate and has not registered at all
(measured 2026-08-12, reproduced twice). The registry is the only authority on
addressability. See `hosts/herdr.md`, which documents the failure and the two
signals that tell the cases apart.

**On the timeout branch, read the screen before you conclude anything** — it is not
optional and it is not a last resort. The host file has the command.

Every other signal in this file is blind to this state: `peer.py` exits 1, and `ASK`,
`ATTN` and `GONE` are all polled from a registry file that does not exist yet, so **no
worker signal fires at all** — measured across a full 63 s timeout. The one line the
watcher does produce inside that window is `WARN`, around 30 s in: the ledger row is on
disk and matches nothing, which is precisely what a worker that never registered looks
like from the registry. It confirms the watcher is watching nobody; it cannot say why.
The screen is the only place the reason is written.

**And you cannot infer the reason, because unrelated causes share one signature.** At
least three produce an identical run — loop spins its whole bound, registry never gains
a record, not one worker signal from the watcher:

- the launch never ran `claude` at all (a host-specific quoting or argument fault —
  the host file names its own);
- the worker started fine and is **parked on a gate** — the folder-trust prompt or
  the bypass acceptance prompt, both under "Permission classes" below;
- the worker was launched into the wrong directory and is gated on *that* directory
  instead of the one you intended.

The first is a bug in what you sent; the others are dialogs waiting for a keystroke,
and the fix for one does nothing for the others. Reading the screen separates them
instantly — a shell prompt with an error above it versus an open dialog, and the
dialog names the directory it is asking about.

### Send the task

Only now, as a `SendMessage` to the address `python3 "$P" "$NAME"` printed:

```
{"to": "uds:/tmp/cc-socks/30580.sock",
 "summary": "review-api: audit the auth middleware",
 "message": "<the whole spec — goal, constraints, what done means>\n\nWhen you are finished, SendMessage your findings to uds:/tmp/cc-socks/<your own pid>.sock. That is the session that sent you this message, and the same address is on this message as its `from`. It is named `<your own name>` — that is a label, not an address; do not send to the name."}
```

The `uds:` string in that reply instruction is **your own** address, not the worker's.
Read it — and the name beside it — out of `$ME`, further down this section; the two
paragraphs after that explain why the `from` sentence alone is not enough to put there.

Put the **whole** spec in that message — goal, constraints, and what "done" means,
plus the instruction to report back. These are one-shot kickoffs; a worker cannot
be clarified as cheaply as a conversation. If the user asked for `ultracode`,
include that word in the text — it is a keyword the worker reads, not a CLI flag.

**Ask for the findings in the reply, not just an acknowledgement.** The reply
interrupts you with its body in it, so a worker that answers "counted 89 lines"
has finished the reporting round trip in one hop. A worker that answers "done"
has sent you back to its transcript for no reason.

**Never tell a worker not to use tools in a message that asks it to reply.**
`SendMessage` *is* a tool call. A blanket "do not read files, run commands, or
investigate anything" — the natural way to keep a small task small — reads to a
literal-minded worker as "do not use tools", and it then satisfies "reply with one
line" by **printing** that line into its own transcript. Nothing arrives, nothing
errors, and from every angle except the one that matters the worker looks finished.
Measured 2026-08-12: two workers were sent the same one-line probe under that wording —
identical but for their own names — and one sent while the other printed. One in two.

Scope the *investigation*, and say the reply is exempt:

```
"Do not read files or run shell commands — but you MUST deliver the reply with the
 SendMessage tool, and loading that tool first if it is deferred in your environment is
 part of the job, not a violation of it. Printing the text as output does not count as
 replying."
```

**The deferred-tool clause is not padding.** Where tool schemas are fetched on demand,
`SendMessage` is not loaded at session start, so replying costs *two* calls — the fetch,
then the send — and a worker told its "only action" is to reply can read the fetch
itself as forbidden and print instead. The worker that succeeded in the measurement
above made exactly that fetch first.

And when you cap the length, cap the reply **body**, not the turn. "Reply with exactly
one line and nothing else" is the clause that makes printing look compliant, because a
worker reads it as a rule about everything it emits; "the reply body must be exactly
one line" cannot be satisfied without sending one.

**Put the literal `uds:` address in the task text. The `from` address is the fallback,
not the instruction.** You already require exactly this of yourself — "Address workers
by `uds:`, never by bare name", above — and the reply direction is the same wall with
the same cure:

```
…When you are finished, SendMessage your findings to uds:/tmp/cc-socks/<your pid>.sock.
That is the session that sent you this message, and the same address is on this message
as its `from`. It is named `<your own name>` — that is a label, not an address; do not
send to the name.
```

**"Use the `from` address on this message" is correct and it is not sufficient.**
Measured 2026-08-12: **2 of 2 workers given exactly that instruction addressed the
supervisor by name anyway**, were refused, and recovered via the ref in the refusal —
each burning a round trip on the one message that carries the results:

```
'smoke-cmux' is not an agent in this conversation. Re-send with the ref to confirm you mean:
  smoke-cmux [72dff0] — Claude session, on this machine, active 5m ago
```

That is byte-for-byte the failure recorded earlier for workers told to reply *by name*
(3 of 3 refused), reached from the opposite instruction. So the variable that decides it
is not "name versus `from`". A worker holds several plausible ways to address you —
the `from` attribute, the name in the prose, a `ListAgents` row — and `SendMessage`'s
own guidance leans toward names. A literal `uds:` string leaves nothing to choose.

**Earlier runs where `from` alone worked are real, and that is the trap.** 5 of 5, then
1 of 1 under herdr, then 0 of 2. It succeeds often enough to look settled and fails into
a silent extra round trip that nothing surfaces unless you grep the worker's screen for
`is not an agent in this conversation`. Treat a clean run as weak evidence here.

**The ref wall is not one-sided.** It applies to a worker addressing you exactly as it
applies to you addressing a worker. What *is* one-sided is the **recovery**: a worker
holds the `from=` address on the message it received and can retry, where you on first
contact hold nothing. That asymmetry is why a refused reply costs a round trip instead
of costing the results.

**A worker cannot look up its own launch name either — do not ask it to.** Measured
2026-08-12, 2 of 2: workers asked to include "the name you were launched with" in a
reply both said outright that they had no read on it, both guessed from `ListAgents`,
and **both guessed a name belonging to a different, concurrent run**. If you want a
worker's identity in the reply body, write the name you launched it with into the task
text yourself. Otherwise join on the transport instead — an incoming message carries
`from-name`, which is authoritative and costs nothing.

Your name and your address are both things the worker cannot look up about you, so read
both from the same record and put both in the task:

```bash
ME=$(python3 - $$ <<'PY'
import json, os, subprocess, sys

def ps(fmt, pid):
    r = subprocess.run(["ps", "-o", fmt, "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip()

pid = int(sys.argv[1])                     # walk up to the claude hosting this shell
for _ in range(8):
    argv0 = (ps("command=", pid).split() or [""])[0]
    if os.path.basename(argv0).startswith("claude"):
        break
    parent = ps("ppid=", pid)
    if not parent or int(parent) <= 1:
        sys.exit("no claude ancestor above pid %s" % sys.argv[1])
    pid = int(parent)
else:
    sys.exit("no claude ancestor within 8 levels of pid %s" % sys.argv[1])

roots = [d for d in os.environ.get('CLAUDE_CONFIG_DIR','').split(':') if d] or [os.path.expanduser('~/.claude')]
for root in roots:
    try:
        r = json.load(open(os.path.join(root, 'sessions', '%d.json' % pid)))
    except (OSError, ValueError):
        continue
    if not r.get('messagingSocketPath'):
        sys.exit("session %d is registered without a messaging socket" % pid)
    print(r.get('name', ''), 'uds:' + r['messagingSocketPath'])
    break
else:
    sys.exit("no session record for claude pid %d under %s" % (pid, ':'.join(roots)))
PY
)
[ -n "$ME" ] || { echo "cannot resolve my own address -- do not send a task that asks for a reply" >&2; exit 1; }
```

**It walks the process tree; it does not take the parent of the shell.** That is the
same walk as the smoke test's staleness gate, and it is here for the same reason: the
naive `ps -o ppid= -p` form is right only when `claude` is the *immediate* parent of
the shell your `Bash` call got. Put any wrapper in between and it names the wrapper.
Measured 2026-08-12 in a cmux surface, session `fixer-b2`, claude pid 27957:

```
direct              parent of the shell is 27957 -> …/claude    ME=[fixer-b2 uds:/tmp/cc-socks/27957.sock]
through one zsh -c  parent of the shell is 36470 -> /bin/zsh    ME=[]
the walk, wrapped   resolved claude pid 27957                   ME=[fixer-b2 uds:/tmp/cc-socks/27957.sock]
```

The walk returned that same answer at zero, one and two extra shell levels.

**An empty answer here is no longer a missing label — it is a missing address.** This
snippet used to return only the human-readable name, so a blank one cost a nicety. It
now carries the `uds:` string the whole reply instruction above is built on, and a
supervisor holding no address falls back to the only other identifier it has: the
worker's name. That is precisely the ref wall the literal address exists to prevent, so
the failure reintroduces the bug two sections up rather than degrading gracefully.

**And empty is the good failure.** `sessions/<pid>.json` is keyed by pid, and pids are
recycled into exactly the range wrapper shells are born in — so a wrapper whose pid
happens to match some *other* live session's record resolves to that session, in full.
Measured 2026-08-12: the old form handed pid 19143 printed
`bin-09 uds:/tmp/cc-socks/19143.sock` — a complete, plausible, wrong answer naming a
live session belonging to somebody else's run. A worker given it sends its findings to
a stranger and nothing anywhere reports a fault. The walk cannot produce that, because
it only ever reads the record of a pid it has confirmed is a `claude`.

So **every branch exits non-zero with a message on stderr** rather than printing an
empty string, and the `[ -n "$ME" ]` line is what turns that into a stop instead of a
task sent with an empty address in it. The doubled `$` in `python3 - $$` is the shell's
own pid and is invariant inside command substitution, so the walk starts where you think
it does; do not replace it with a captured parent.

It honours `CLAUDE_CONFIG_DIR` rather than hardcoding `~/.claude` — **every
colon-separated segment of it, in order, exactly as `peer.py` and the watcher do**, so
all three agree on where a session may live. See "Several config profiles" below, and
note that a second profile really does exist on this machine, so the hardcoded form
returns an empty name for every session in it, and reading only the first segment
returns an empty name whenever the caller's own profile is the second one. It refuses a
session registered without a `messagingSocketPath` for the same reason the collision
check does — that record cannot be replied to at all (see the registry section above).

It resolves the **hosting CLI session** by construction, since that is what the walk
looks for: run from inside a subagent it prints the *host's* name, not the subagent's,
so nested orchestration that asks workers to reply by this name routes their findings to
the wrong session. One more reason the `from` address is the fallback sentence and this
name is only a label — the address beside it is the part that must be right.

## Spawn several at once

One slot per worker, placed by the host file's fan-out rule — which exists because
the hosts differ on what a fan-out costs the user, and getting it wrong shrinks
what they are already reading once per worker.

The core obligations do not change, and they are easy to get backwards in a loop
because the bookkeeping reads like it follows the interesting line:

- the **per-name collision check**, once per worker;
- the **ledger row before the launch**, once per worker — instrumented with the two
  orders swapped, the gap was real and observable: a launch dispatched while the
  ledger still held exactly one row naming only the first worker. Every worker after
  the first spends that window as an unrecorded live agent;
- **resolve and guard both locators** before writing the row.

Then send each one its task with its own `SendMessage`, once `python3 "$P" "$NAME"`
answers for it.

**Do not collect the names in a space-joined string** — `for n in $NAMES` iterates
once in zsh, not once per worker, and you will drive one agent while believing you
drove four. The ledger is the list: re-read it with
`while IFS=$'\t' read -r name l1 l2 state`.

Never infer which worker is which from tab or pane order; the hosts order new slots
differently and one of them does not append. The name is the title, which is the
only thing that distinguishes four identical terminals — so name them for the tab
bar, because that is how the user reads a fan-out.

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

There is a third way it fails to arrive, and **no watcher signal names it**: a worker
that *printed* its reply instead of sending one has done nothing the registry can see as
wrong. It took a turn and went idle. The best you get is a `DONE`, and you may not even
get that — `DONE` is a sampled `busy → idle` transition, and a print-only turn is the
shortest one a worker can take, so it can open and close inside a single poll.

So the trigger is the absence of a signal, not the presence of one: **you are owed a
reply and it has not come.** Before re-sending, ask whether the worker is idle and then
look at what it actually did:

```bash
python3 "$P" "$NAME" status     # idle, with no reply in hand, is the signature
```

The evidence is spelled differently in the two places you can look, and only one of them
is a file:

| Where | A send that really happened |
| --- | --- |
| the worker's **screen** (the host file's read command) | a `SendMessage` call with `⎿ … → uds:/tmp/cc-socks/<pid>.sock` under it |
| the worker's **transcript** (see "Read a worker's output") | a `tool_use` block named `SendMessage` — the `⎿` glyph is terminal rendering and is never in the file |

**Prefer the transcript, and on one host it is the only option.** Those rows are not
equal: a send that has scrolled off is still in the file and is no longer on the screen,
and Claude Code draws on the alternate screen, whose history a host may not be able to
reach at all. cmux cannot — every scrollback flag it has returns the current viewport,
so a grep comes back clean over a refusal sitting just above it. herdr can, but only
while the worker is idle. Both host files give the measurement and the exact command;
read yours before you conclude anything from a screen.

Read it by the tool call, not by the address. **No `SendMessage` call at all** means the
task's own wording talked the worker out of the tool — see "Send the task" above. A
`SendMessage` call whose result says `is not an agent in this conversation` is the
opposite fault, the ref wall, and its fix is the literal `uds:` address in the task
text rather than any change to the prohibitions.

### The watcher — one Monitor, five worker signals

```bash
python3 -u "${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/watch-workers.py" \
    "${TMPDIR:-/tmp}/spawn-agent/<CALLER_SLOT>.tsv"
```

**Write the slot id into that command literally** — expand `<CALLER_SLOT>` yourself
before arming it. `${TMPDIR:-/tmp}` stays as written (see below); the slot does not,
because `Monitor` runs its own shell and no assignment you made in a `Bash` call reaches
it. Measured 2026-08-12: an exported `CALLER_SLOT` read back empty inside a `Monitor`
command.

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
empty string, the watcher is handed `""` as its ledger path, and every worker is
filtered out as unknown. You get a watcher that runs happily and reports nothing
about any worker — for the first 30 s, indistinguishable from a run where nothing
has finished yet. Then it says so: `WARN ledger unreadable, watching nothing:`
with nothing after the colon, which is the empty path telling you exactly this.
Treat an empty path in that line as the diagnosis, not as a puzzle.

**`$CALLER_SLOT` is the one that bites, and exporting it does not save you.**
`TMPDIR` is in that shell's environment because it is in the *process* environment,
inherited from the terminal. `CALLER_SLOT` is a name this skill invents inside a `Bash`
call — and a `Bash` call's shell state does not outlive the call, `export` included.

Measured 2026-08-12: `export CALLER_SLOT_PROBE=probe-value` in one `Bash` call read back
**empty** in the next `Bash` call *and* **empty** in a `Monitor` command, while
`$CMUX_SURFACE_ID` — a genuine environment variable — was visible in both.

So there is exactly one form that works here: **write the expanded slot id into the
`Monitor` command literally**, the same way you write the plugin root. Bind it, echo it,
paste the value.

And the same rule governs every *other* `Bash` call in this skill: re-derive the slot in
each block that needs it, from `$CMUX_SURFACE_ID` or `$HERDR_PANE_ID`, which survive
precisely because they are in the process environment and `CALLER_SLOT` is not. Carrying
the binding forward from an earlier call is the empty-path failure with extra steps.

**Write `${TMPDIR:-/tmp}/spawn-agent/…` here and in the `LEDGER=` assignment
character for character, and do not tidy either one.** `TMPDIR` on macOS ends in a
slash, so both render a doubled one — measured here as
`/var/folders/…/T//spawn-agent/…`. It is pure cosmetics: the doubled and single
forms are `os.path.samefile`, so nothing is broken and there is nothing to fix. The
trap is the fixing. These two paths agree only because they are the *same*
expression, so the tempting cleanups both break the pairing — and the worst is
substituting the literal `/tmp` you can see in the default, because on macOS
`TMPDIR` is **not** `/tmp` (`samefile` says `False`). The watcher then polls an empty
directory while the spawn side writes rows somewhere else: one `WARN` at 30 s, and
every signal in the run lost. If the doubled slash bothers you when you paste it,
leave it bothering you.

Each output line becomes a chat notification that re-invokes you. **Five of them
are worker signals** — one named worker, one state change:

- `DONE <name>` — that worker went from **working to idle**, so a turn ended.
  Not the same as "the work is finished": a worker parked awaiting its own
  background sub-agent ends its turn too. The worker's own reply is what tells you
  the outcome.
  **Expect that reply to arrive *before* this line, not after.** The worker sends
  it as its last act and delivery is immediate, while `DONE` waits on the next
  poll, up to one poll interval (2 s by default) later — measured in that order on
  2026-08-09. So `DONE` is a backstop for a worker that finished without reporting,
  and a supervisor that gates on `DONE` before reading the findings is waiting for
  something it is already holding.
- `ASK <name>` — **suspended** on an `AskUserQuestion`. Not an ending, and no
  `DONE` follows until someone answers it.
- `ATTN <name>` — suspended on a permission prompt, a plan approval, or a held
  peer message. Not an ending either.
  **Its absence proves nothing about the worker's mode.** A worker launched
  `--permission-mode manual` ran a read-only `find … | wc -l` with no prompt at
  all, and no allow-rule explained it — the settings had an empty `allow` list
  (measured 2026-08-09). So a quiet run is not evidence the watcher is deaf.
  **And there is no reliable one-liner for provoking one.** Writing is not enough:
  three deliberate probes under `defaultMode: auto` with an empty
  `permissions.allow` raised no prompt at all. To *see* a worker parked for a
  human, use a `manual`-mode worker and give it a command outside its allow list
  (`ls /usr/share/dict` did it on 2026-08-12), or use the pre-registration gates
  below.
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
same run produced. So five are defined and four have been caught in the wild —
`CLEAR` is the one still unobserved there, and the rarest by construction, since it
needs a worker to stop being blocked without taking a turn.

**`GONE` is the signal an event bus cannot give you.** A worker that dies outright
— `SIGKILL`, a host crash, a slot torn down under it — never runs a hook, so
nothing is ever published about it. Measured 2026-08-07: a worker `SIGKILL`ed
while parked on an `AskUserQuestion` produced no bus event whatsoever, and the run
looked exactly like a worker still sitting on its question. Polling the registry
for pid liveness is what closes that hole, and it is why this watcher is built on
process state rather than on events. It is also why the watcher is not retired by a
host that publishes its own lifecycle states — see the note at the end of this
section.

**A sixth line exists, and it is not a worker signal.** That distinction is the
whole of how to read it: `WARN` names no worker and says nothing about any
worker's state. It is the watcher reporting on *itself*.

- `WARN ledger has N row(s), none match a live session name: <path>` — and its
  two siblings, `WARN ledger is empty, watching nothing: <path>` and
  `WARN ledger unreadable, watching nothing: <path>`. All three mean the same
  thing: the ledger this watcher was armed on matches no live session, so it is
  watching nothing and will report nothing about anyone. It fires once, about
  30 s in — late on purpose, because the ledger row is written *before* its
  worker is launched, so "no match yet" is the normal state while a `claude`
  boots. Check two things when it lands: that the ledger path expanded (a
  `$CALLER_SLOT` that came out empty in the `Monitor`'s own shell is the usual
  cause), and that the rows are the current four-column format, since an older,
  differently shaped ledger puts something that is not a name in column 1.

It is emitted **at most once per run** and never at all once anything has
matched, so silence after the first half-minute is the positive signal that the
watcher is aimed at something real. Do not read a `WARN` as a worker in trouble,
and do not wait for a second one.

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
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr -- use your host's line, not both
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- refusing an unscoped pattern"; exit 1; }
pgrep -f "watch-workers.py.*${CALLER_SLOT}" >/dev/null \
  || echo "watcher is not running -- re-arm it before sending the task"
```

**Keep the slot id in that pattern — a bare `pgrep -f watch-workers.py` answers
one too many.** Measured 2026-08-09: a bare `pgrep -f watch-workers.py | wc -l`
answered `2` for a session that had exactly one watcher, and the author of that
check read it as proof a second agent had armed its own. It had not.

**Which is why the binding and the guard are in the block rather than assumed.** An
unset `CALLER_SLOT` does not error — it expands to nothing, leaving `watch-workers.py.*`,
which is exactly the bare form this paragraph forbids, reached by accident instead of by
choice. The guard turns a silent widening into a refusal.

The extra process is **not** the shell running the check — that was tested three ways,
and a unique token planted in the checking shell's own argv was never matched by
`pgrep -f` from that same shell, ruled out at argv offsets up to 2013. It is the
watcher's *own* host shell: `Monitor` runs the command under a `zsh -c`, whose command
line contains the whole `python3 -u …watch-workers.py …` string as well. One watcher,
two matching processes.

That is also the scoped form's blind spot, and worth knowing before you lean on it.
The host shell's argv holds the **unexpanded** variable — the expansion happens inside
it — so the scoped pattern can never match that shell. It answers yes-or-no about your
python watcher and says nothing at all about an orphaned host shell.

### A host that publishes its own agent states does not replace this

herdr classifies the agent in each pane as `idle` / `working` / `blocked` / `done` /
`unknown`, and offers a **blocking wait** on those states — which is genuinely
better than polling for the blocked case, and `hosts/herdr.md` tells you to use it.
It does not retire the watcher, for three reasons:

- **It cannot report a death.** A killed worker leaves a pane herdr classifies as
  `unknown`, which its own documentation says "does not prove completion" — and
  does not prove death either. `GONE` comes from the pid check and nothing else.
- **It is a screen classifier, not the session's own state.** The registry is
  written by the session about itself.
- **It covers only what the host hosts.** The registry covers every profile and
  every worker, including ones spawned by a stage running somewhere else.

Where both are available, use the host's wait to *block* on a specific worker and
the watcher to be *pushed* about all of them. They agreed exactly on 2026-08-12:
herdr `blocked` ↔ registry `waiting` / `permission prompt`, on the same worker at
the same moment.

## Answer a blocked worker

`ASK` and `ATTN` both mean *a human is being waited on, and the run is stopped
until one shows up*. Neither line says what was asked. **Read the screen first**,
then answer with keys — both commands are in the host file.

**Reading first is not a diagnostic nicety, it is the whole procedure.** Which keys
are right depends entirely on which row is already highlighted (`❯`), and dialogs in
this product routinely open with the safe or affirmative option pre-selected, so a
reflexive `down` moves *away* from it. Count the highlighted row in the screen output
and send only the keys that get you from there to the row you want; on a dialog that
opens where you already want to be, that is `enter` alone. The pre-registration gates
below are where this costs the most: the wrong extra `down` selects "No, exit" and
kills the worker outright.

**`SendMessage` cannot clear a block, and it does not fail loudly when you try.**
Measured 2026-08-09: a message sent to a worker parked on an `AskUserQuestion`
returned `success: true`, appeared on that worker's screen as queued text *beneath*
the open dialog, and was not read until after the question had been answered by
key — at which point it arrived appended to the tool result. So `success` means
*handed to the session*, never *read by Claude*. A supervisor that answers `ASK`
with a message will sit forever watching a worker that has already been told.

Send keystroke calls **one per `Bash` call**, not bundled into a script. Observed
repeatedly on 2026-08-09: the auto-mode classifier denies the compound form and
sometimes the single form too. The single form succeeds on retry; a denied
keystroke leaves the worker blocked and looks exactly like a worker that ignored
you.

**A worker's input box may show text nobody typed.** A screen read renders
suggested-follow-up ghost text in the prompt exactly like real input, and a queued
peer message sits there too. Neither is pending user input, and pressing `enter`
on either submits it. Judge from the dialog, not from the prompt line.

## Chain stages

Same session, next task — context carries over, so use this when the next stage
needs what the previous one found. Prose goes by message; **a slash command must
go by keys**, using the host file's keystroke channel.

**Slash commands do not execute over cross-session messaging.** Verified
2026-08-09 by sending `/context` to a worker: it arrived as literal text inside
the message wrapper, no expansion, nothing run. Expansion happens in the CLI on
input the user types; a message is injected past that path. Verified from the other
side on 2026-08-12: the same `/context` delivered through a host's keystroke channel
rendered the real context breakdown. The nuance that makes this a trap rather than a
clean failure: a worker that reads "run /foo" may still *choose* to invoke `foo`
through its `Skill` tool, so a plugin skill can appear to work by persuasion while a
built-in like `/context` or `/compact` can never run at all. Do not let one lucky
stage convince you the channel expands commands.

Fresh session for a stage that should start clean: another slot, placed exactly as
the first one was. It shares no context, so **pass the handoff explicitly** — name
the file the previous stage wrote, rather than referring to "the findings."

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
  the keystroke channel is unaffected by any of this — and keep the watcher for its
  state.
- The product's own held-message dialog names the escape hatch,
  `crossSessionInbound: "accept"`, settable per worker via
  `--settings '{"crossSessionInbound":"accept"}'`. **Untested here** — the launch
  line was refused by the auto-mode classifier — and it only fixes the inbound
  half, since the reply direction is governed by the *supervisor's* setting, which
  is the user's to change and not yours.

To spawn a worker in a specific permission class, put the flag on the launch line
the host file shows. `--permission-mode` takes `acceptEdits`, `auto`,
`bypassPermissions`, `manual`, `dontAsk` or `plan`. All but `bypassPermissions` are
the prompting class.

Note also that **the registry does not record a session's permission mode**, so
you cannot tell a bypass worker from its JSON. What you can read is the
`from-mode` attribute on a message that arrives from it.

### Two gates can hold a worker before it ever registers

No signal in this file can see either one. With the session not yet started there is
no registry entry and no socket, so `peer.py` exits 1, the watcher polls a file that
does not exist, and the readiness loop simply times out. **Reading the screen is the
only thing that says which gate it is** — which is why reading it on that timeout is
mandatory and not a last resort.

The first is the **bypass acceptance gate**: `--dangerously-skip-permissions` opens a
"Yes, I accept" confirmation before the session starts.

The second is the **folder-trust gate**, and it is the one that surprises, because
nothing about the launch hints at it:

```
 Quick safety check: Is this a project you created or one you trust?
 ❯ 1. Yes, I trust this folder
   2. No, exit
```

**Expect that plain form, and read option 2 before you touch anything** — it has two
variants and they do not do the same thing. The `⚠ This folder pre-approves N tool
permissions in .claude/settings.json` line appears **only** when the target has a
`.claude/settings.json`, and it comes with the softer second option:

```
 Quick safety check: Is this a project you created or one you trust?
 ⚠ This folder pre-approves 21 tool permissions in .claude/settings.json
 ❯ 1. Yes, I trust this folder
   2. No, continue without these permissions
```

Both are real; the plain one is what a checkout without a settings file renders, and
it is what was measured on this machine, twice, most recently 2026-08-12. Option 2 is
"continue with fewer permissions" in one and **"exit"** in the other, so the same
keystroke either degrades the worker or destroys it.

It fires for any `$REPO` that profile has not opened before. **Being a valid git
checkout does not exempt it, and it happens under default permissions** — measured on
a clean checkout with no flags at all: the worker parked here, never registered, the
readiness loop exhausted every one of its 60 iterations, and across the whole 63 s not
one *worker* signal fired.

Three ways out, and the third is the one worth knowing:

- **`enter` alone** answers option 1 on both gates, which are pre-selected there.
  Read the screen and count the `❯` row first anyway.
- **Do not replay a `down`-then-`enter` sequence here.** On the trust gate that extra
  `down` moves the selection to option 2, which in the plain variant is **"No, exit"**
  — so the keystroke meant to rescue a parked worker terminates it instead, leaving a
  ledger row on disk and a slot holding a dead shell. A recoverable gate becomes an
  unrecoverable one. Caught in a live run on 2026-08-09.
- **`esc` cancels the gate and exits `claude` cleanly, without trusting anything.**
  Measured 2026-08-12. That is the right answer when the directory named in the dialog
  is *not* the one you meant to launch into — pressing `enter` there would grant read,
  edit and execute in a folder the user never approved, and the dialog names the
  directory precisely so you can check.

**Never let a keystroke channel answer a gate by accident.** On a host whose launch
command reports success while the worker is still gated, a task sent as keystrokes
lands *on the dialog*: measured 2026-08-12, the prompt's trailing Enter selected
"Yes, I trust this folder", the task text was swallowed, and the worker sat idle with
no transcript file at all — zero turns. Three failures in one call, and the call
returned success. This is exactly what the registry-based readiness loop prevents, and
why it is not optional on any host.

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
may need forensics, read the `sessionId` while it is still alive. Its absence is
also positive evidence in its own right: a registered worker with no transcript has
taken no turn, which is how the swallowed-task failure above was proved.

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

When the last stage is reported, the ledger rows are spent slots. Resolve each
one, then **offer** — cleanup is a proposal, never a side effect of finishing:

```bash
while IFS=$'\t' read -r name l1 l2 state; do
  # resolving l1 is the host file's job; empty means the user already closed it
  reg=$(python3 "$P" "$name" status)      # empty = no live session by that name
  sid=$(python3 "$P" "$name" sessionId)   # capture NOW; unreadable once it exits
  echo "$name  ledger=$state  registry=${reg:-gone}  resume=${sid:-none}"
done < "$LEDGER"
```

Show that list with what each worker did, and close only the ones the user
confirms, with the host file's close command.

- The scrollback dies with the slot; the session does not.
  `claude --resume <sessionId>` brings it back — that id is unreadable once the
  process is gone, so capture it before you close anything.
- `busy` or `waiting` means not done. A `waiting` worker is stopped on a prompt and
  will sit there forever — closing it discards whatever it was about to do. Leave
  those open, and say which ones you left and why.
- `registry=gone` on a row you never reported is a worker that died mid-stage.
  Lead with those.
- **Confirm every close by re-resolving the locator, never by reading what the close
  command echoed back.** One host prints an allocation counter that has nothing to do
  with the slot you just closed; the host file says which.
- Prune closed rows from the ledger so the next offer is not a list of ghosts. Match
  on locator 1, and rewrite through a temp file — `sed -i` is the improvised
  answer here and it is not portable to the BSD `sed` on this machine:
  `[ -n "$l1" ] && { grep -v -F "$l1" "$LEDGER" > "$LEDGER.tmp"; mv "$LEDGER.tmp" "$LEDGER"; }`
  **Both halves of that line are load-bearing, and they fail in opposite directions —
  learn only one and you will write the other bug.**
  - **Join the `grep` and the `mv` with `;`, never with `&&`.** `grep` exits 1 when it
    selects no lines, so the `&&` form skips the `mv` exactly when the result is empty
    — the last row of any ledger, which on a single-worker run is the only prune the
    run ever does. The ghost row survives and a stray `.tmp` is left beside it.
  - **Guard `$l1` first.** That is not defensive padding; it is what stops the same
    one-liner shredding the ledger. `grep -v -F ""` selects *nothing*, and with the
    `mv` now unconditional that nothing is installed over the whole file. Empty `$l1`
    is reachable by following this file rather than by ignoring it: it comes from the
    loop above, the prune reads as a standalone command, and variables do not survive
    to the next `Bash` call — so a supervisor that prunes in a fresh call has it unset
    and destroys the record of every slot the run owns. Measured both ways: unguarded,
    a three-row ledger goes to **0 rows and 0 bytes while exiting 0** — the destruction
    reports success, so nothing upstream can notice it, and the `&&` form's exit 1 was
    the only tripwire there ever was; guarded, it is untouched.

  That asymmetry is the whole point. The `&&` bug leaves one ghost row and the next
  cleanup offers a slot that is already closed. The unguarded bug erases which slots
  the run owns at all, and "only ever propose slots from this run's ledger" then
  makes every one of those slots unofferable forever. One is noise; the other is the
  exact failure the ledger exists to prevent.
- A dead worker leaves its `<pid>.json` behind. It is not yours to delete, and
  `peer` and the watcher both ignore it on the pid check.

Only ever propose slots from this run's ledger. A tab you did not spawn is the
user's, however idle it looks — leave it alone even when it is obviously a dead
agent from an earlier session.

### Finish the run — four steps, in this order

Closing the slots is not the end. The watcher is a *process*, and a `Monitor`
armed with `persistent: true` runs until `TaskStop` or the end of the session that
armed it — and **if you are yourself a spawned agent, your session ending does not
reap it.** Observed: an agent finished, its slot was closed, and its watcher was
still polling.

**Stop it first, before you close anything.** Once every worker has been reported the
watcher has nothing left to tell you, while each slot you close under a live one is
a worker dying on purpose — which it correctly reports as `GONE`. Measured: three
closes with watcher and ledger both still live produced exactly three `GONE` lines,
all naming workers already reported and deliberately closed. Nothing is wrong when
that happens; it is one "a worker died" notification per slot, arriving exactly as you
tell the user the run finished cleanly. Close first if you prefer — but then expect
one `GONE` per slot and read it as benign rather than chasing it.

1. **`TaskStop` the monitor** by the task id you were given when you armed it.
2. **Close each slot** you spawned, as above — resolving by locator, never by the
   close command's echo.
3. **Delete the ledger file**, not just its rows — re-deriving the slot in that same
   call, since an empty `$CALLER_SLOT` deletes `…/spawn-agent/.tsv`, reports success,
   and leaves the real ledger exactly where it was:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr -- use your host's line, not both
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- the real ledger would survive this"; exit 1; }
rm -f "${TMPDIR:-/tmp}/spawn-agent/${CALLER_SLOT}.tsv"
```

   This is the
   belt-and-braces step, and what it buys is *bounded noise*, not silence. A watcher
   that somehow survives the `TaskStop` finds no ledger, so it matches no name — and
   every worker it had seen alive is then two missed polls from `GONE`. Reproduced by
   deleting the ledger under a live watcher: one death notification per worker in the
   run, all at once, and after that it has nothing left to say ever again. Expect that
   burst if step 1 did not take. It names workers you have already reported, it is as
   long as the run was wide, and it does not repeat.
4. **Confirm nothing of yours is left**, scoped to your own slot:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr -- use your host's line, not both
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- refusing an unscoped pattern"; exit 1; }
pgrep -fl "watch-workers.py.*${CALLER_SLOT}"
```

**Scope that `pgrep` — do not run it bare.** `pgrep -fl watch-workers.py` lists every
watcher on the machine, including live ones belonging to other sessions and other
projects, wrapped in 400-character zsh preambles you then have to read. That is not a
check, it is an invitation to kill someone else's run: an agent doing exactly this
reported two healthy watchers as orphans — one of them its own supervisor's. Scoped to
a non-empty `$CALLER_SLOT` the answer is yes-or-no and cannot implicate anyone else.

**And "bare" is reachable without typing it.** `CALLER_SLOT` does not survive from the
`Bash` call that bound it, so left unbound here it expands to nothing and the pattern
collapses to `watch-workers.py.*` — the machine-wide form, silently. That is why the
binding and the emptiness guard are inside the block. This is the one place in the
skill where the widening is not merely wrong but destructive, because of what comes
next.

If a line does come back after `TaskStop`, it is yours and it is stuck:

```bash
CALLER_SLOT="$CMUX_SURFACE_ID"                  # cmux
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # herdr -- use your host's line, not both
[ -n "$CALLER_SLOT" ] || { echo "STOP empty slot -- refusing to pkill unscoped"; exit 1; }
pkill -f "watch-workers.py.*${CALLER_SLOT}"
```

Re-derive and re-guard here too, in this block, even though you just did it above —
this is a `pkill`, and an empty slot turns it into "kill every watcher on this machine,
including the ones belonging to sessions that are still running."

**Never reap a watcher you cannot prove is dead.** A watcher is a true orphan only if
the `claude` process that armed it is gone — check with
`ps -o ppid= -p <pid>` and see whether that parent still exists. A live session's
watcher looks identical in `pgrep` and killing it silences a run that is still going,
which is the one failure this whole section exists to prevent.

## Rules

- **"Spawn" means a visible session, not a subagent.** Once the user has used that
  word, an invisible subagent is not a cheaper version of this — it is a different
  thing, one they cannot watch, click into, or take over. Reaching for `Agent`
  because the tasks look small is the one substitution to refuse: small tasks are
  exactly what a showcase is made of.
- **Decide the host by precedence, before anything else.** `HERDR_ENV=1` wins over
  a present `CMUX_SURFACE_ID`, because inside herdr that variable is live, valid,
  wrong, and identical for every pane on the machine.
- **The name is the key.** `claude -n <name>` at launch, unique among live
  sessions, `[a-z][a-z0-9_-]{0,31}` so it works on every host, and every lookup
  afterwards goes through it.
- **Address by `uds:`.** A bare name is refused on first contact and costs a round
  trip to recover the ref from; the socket path is derivable from disk and always
  works.
- Anchor placement on your own slot, never on what is focused — the user may be
  looking elsewhere, and both hosts have a command that quietly targets the focused
  thing when you leave the target off.
- **Readiness is the registry, never the host's word for it.** One host returns
  "ready", exit 0, for a worker parked on a gate that has not started a session at
  all.
- **Arm the watcher at the first spawn, before sending any task** — one `Monitor`
  running `watch-workers.py` against the ledger, for one worker or for ten. The
  `Monitor` **tool**: the same command backgrounded with `Bash` streams into a file
  nobody reads and notifies you only if it exits, which it never does. The worker's
  own reply is not a substitute, because a worker that is blocked or dead is
  precisely the worker that cannot send one. A host that publishes its own agent
  states does not replace it either — nothing but the pid check reports a death.
- **Ask every worker to report its findings by message**, writing your own literal
  `uds:` address into the task text. "Use the `from` address on this message" is the
  fallback sentence, not the instruction: told only that, 2 of 2 workers addressed the
  supervisor by name anyway and were refused, exactly as 3 of 3 were when told the name
  outright. That reply is the only signal that carries content.
- **`DONE` means a turn ended, not that the work is done** — and the worker's
  reply usually arrives *before* it. Collect findings from the reply; treat `DONE`
  as the backstop for a worker that finished without reporting.
- **Messages cannot clear a block and cannot run a slash command.** Keys can do
  both, one `Bash` call per keystroke.
- **Keep workers in your own permission class**, or messaging silently stops in
  both directions.
- **The ledger is on disk**, at `${TMPDIR:-/tmp}/spawn-agent/<caller slot>.tsv` —
  keyed by the caller's slot, so it is exactly this run's spawns and nothing else.
  That is what lets a turn which remembers nothing still answer what it owes and
  what it may close.
- Report each stage's outcome as it lands; don't go silent for a long pipeline.
  Mark the ledger row `reported` when you do.
- Never close a slot you did not spawn, and never close one without asking —
  not even your own.
- **A run ends when its watcher is stopped, not when its last slot closes.**
- Before a destructive or long-running task, say which repo and which profile it
  will run in and get confirmation.
- Workers inherit the user's global `~/.claude/CLAUDE.md`. If you plan to parse a
  worker's reply, expect whatever that file makes every session emit.
