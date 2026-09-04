# Host — herdr

The commands `spawn-agent` needs when `HERDR_ENV=1`. Read this alongside `SKILL.md`,
not instead of it: the peer registry, messaging, the watcher, permission classes and
the ledger all live there and are not repeated here.

Everything below was measured on 2026-08-12 against herdr client and server 0.8.0,
protocol 19.

**One command per `Bash` call here too.** The blocks are grouped for reading.

**herdr wins even when `CMUX_*` is set, and that is not a tie-break — it is a
correctness rule.** See §0 of `SKILL.md`: a herdr pane inherits the environment of
whatever started the herdr *server*, so `$CMUX_SURFACE_ID` here names a live cmux
surface — the one displaying the herdr window — and every herdr pane on the machine
carries the same value. Using it would key every run's ledger to one shared file and
aim every placement command at the herdr window, returning `OK` each time.

## 0. Two things about the CLI itself

- **Exit codes discriminate.** A server error is JSON on **stderr** with exit **1**
  (`{"error":{"code":"pane_not_found",…}}`); a CLI syntax error exits **2**
  (`unknown option: -n`). So exit 2 means you wrote the command wrong and no state
  changed; exit 1 means herdr understood you and refused.
- **A missing target means "the focused pane".** Any command whose `pane_id` is
  optional falls back to the server's *active focused pane*, which can belong to the
  user or another client. Always pass an explicit id, or `--current` when you really
  mean the calling pane. This is herdr's exact analogue of cmux's "never place by
  `focused`".

Ids are opaque and you must not pattern-match them. A freshly started server here
came up as workspace **`w9`**, not `w1`. Ids are **not reused**: closing `w9:t3` /
`w9:p4` and creating again produced `w9:t4` / `w9:p5`.

## 1. Bind the caller's slot

herdr nests the opposite way to cmux: a **tab** contains **panes**. The slot is the
pane this session occupies.

```bash
[ "${HERDR_ENV:-}" = 1 ] && [ -n "$HERDR_PANE_ID" ] && [ -n "$HERDR_WORKSPACE_ID" ] || {
  echo "not in a herdr pane (need HERDR_ENV=1, HERDR_PANE_ID, HERDR_WORKSPACE_ID); not spawning" >&2
  exit 1
}
WS="$HERDR_WORKSPACE_ID"
CALLER_SLOT="${HERDR_PANE_ID//:/-}"             # w9:p2 -> w9-p2
```

**The `:` must go.** `$HERDR_PANE_ID` is a path component in the ledger path, and a
colon in a filename is legal at the POSIX layer but displays as `/` in Finder and
breaks anything that splits on `:`. Sanitise once, here, and every later use — the
ledger path, the watcher's `pgrep` pattern — agrees automatically.

**Repeat those two lines at the top of every later `Bash` call that needs them, and do
not try to `export` your way out of it.** A `Bash` call's shell state does not outlive
the call — measured 2026-08-12, an exported variable read back empty both in the next
`Bash` call and inside a `Monitor` command. `$HERDR_PANE_ID` and `$HERDR_WORKSPACE_ID`
survive because they are in the process environment, which is exactly why the two names
above are derived from them rather than carried forward. For the `Monitor` command,
write the expanded slot id in literally.

`$HERDR_PANE_ID` satisfies the four slot properties in `SKILL.md`: unique per pane,
not reused after a close, durable across a `claude` restart inside that pane, and
filename-safe once sanitised.

herdr needs **no resolver script**. `herdr pane get <id>` is the liveness oracle that
cmux needs `cmux-surface.py` for:

```bash
herdr pane get w9:p4      # {"error":{"code":"pane_not_found",…}}  exit 1
herdr pane get w9:p6      # a PaneInfo                              exit 0
```

## 2. Ledger locators

| column | holds | resolve with |
| --- | --- | --- |
| 2 | the worker's **pane id** (`w9:p3`) | `herdr pane get "$l1"` — exit 1 means the pane is gone |
| 3 | the worker's **terminal id** (`term_658d4ecc9f1202`) | recorded at creation; never reused |
| 7 | the literal `herdr` | not resolved — `SKILL.md`'s setup block writes it from §0's detection. Never typed here |

Column 3 is an identity witness, not a navigation handle. Pane ids are not reused
*within a server session*, but durability across a **server restart is unmeasured** —
and the ledger outlives a restart, because it is a file. The terminal id is what lets
a later run notice that `w9:p3` is not the `w9:p3` its row was written about.

**Rows 2 and 3 mean what this table says only for a row whose column 7 is `herdr`.** A
row tagged otherwise holds another host's ids, and `herdr pane get` on one exits 1 with
`pane_not_found` — which §9 has, until now, defined as "the user already closed it".
Nothing in this file binds column 7: it is derived once, in `SKILL.md`'s setup block,
from the same expression §0 used to send you here, so that the tag and the choice of
this file are a single decision rather than two that can disagree.

## 3. Where a worker goes

A herdr tab costs the user nothing: `--no-focus` genuinely does not steal focus, and
a new tab shrinks nothing that is already on screen. A *pane* split does shrink. So
the default here is the opposite of cmux's:

| Asked for | Layout |
| --- | --- |
| one agent | its own tab, `--no-focus`, labelled with the worker's name |
| several at once | one tab each — a fan-out costs the user nothing |
| "in a pane", "split it off" | `herdr pane split --current`, as asked |

**Why not panes by default.** Measured here, a split pane was ~28 columns wide and a
screen read of it wrapped into unreadable ribbons, while a full-width tab gave 54
rows of clean text. herdr's own guidance warns that repeated same-direction splits
produce unusably narrow columns, which caps a pane fan-out at two or three; tabs have
no such cap. And the user reads a fan-out by its labels either way.

```bash
# One tab per worker. --cwd and --label are both load-bearing; see below.
OUT=$(herdr tab create --workspace "$WS" --cwd "$REPO" --label "$NAME" --no-focus)
L1=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
L2=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["root_pane"]["terminal_id"])')
[ -n "$L1" ] && [ -n "$L2" ] || { echo "no pane came back; not launching" >&2; exit 1; }
```

The tab id is in `.result.tab.tab_id` if you want it for teardown; closing the tab is
the tidiest close, and it is derivable from the pane at any time via
`herdr pane get "$L1"`.

When the user did ask for a split:

```bash
herdr pane split --current --direction right --cwd "$REPO" --no-focus
```

Read the new pane from `.result.pane.pane_id`. Split a wide pane `right` and a narrow
or tall one `down`; `herdr pane layout --pane "$HERDR_PANE_ID"` tells you which.

**`--cwd` is not optional, and omitting it is the herdr version of cmux's dropped
`cd`.** Measured: `herdr tab create` *without* `--cwd` put the pane in `/Users/ns` —
the workspace default, **not** the caller's cwd — which then tripped the folder-trust
gate on the home directory. Same class of bug as cmux's, reached by a different
route.

What herdr does remove is the entire *quoting* half of that bug. `--cwd` is an argv
element, not a fragment of a shell string, so a path containing a space is passed
verbatim and there is nothing to escape. Verified with a scratch repo at
`…/scratchpad/herdr probe repo`: the pane's `cwd` came back byte-identical. cmux's
`zsh:cd:1: string not in pwd` failure cannot occur here.

An explicit `--label` survives; the pane's `terminal_title` separately becomes
`✳ <name>` once the agent is up. Pass `--label "$NAME"` so the tab bar reads the
worker's name — with one tab per worker that label *is* how the user tells them
apart.

## 4. Launch

**Check the herdr namespace first — `SKILL.md`'s collision check cannot see it.**

```bash
herdr agent get "$NAME" >/dev/null 2>&1 && { echo "a live herdr agent is already named $NAME" >&2; exit 1; }
```

`peer.py` reads Claude Code's session registry. herdr keeps a **separate,
server-global agent registry** covering 21 kinds — `herdr agent` lists them, and
they include `codex`, `gemini`, `cursor`, `copilot` and 16 more. **Only the
`claude` ones ever register with Claude Code**, so `peer.py` is structurally blind
to 20 of the 21. A user who hand-started `codex` in a herdr pane and called it
`review-api` is invisible to the pre-launch check in `SKILL.md`; the ledger row
gets written, and every later `herdr agent … "$NAME"` in §5–§8 resolves to **their
pane**. `agent get` exits 1 with `agent_not_found` when the name is genuinely free.

```bash
herdr agent start "$NAME" --kind claude --pane "$L1" -- -n "$NAME" --session-id "$SID"
```

With a permission class, after the same separator:

```bash
herdr agent start "$NAME" --kind claude --pane "$L1" -- -n "$NAME" --session-id "$SID" --permission-mode manual
```

### Remote control — opt-in, and only when the caller asks

A caller that wants the worker reachable from a phone or claude.ai/code supplies a
remote-control name; nothing else changes. Append `--remote-control "$NAME"` after
the same separator, to whichever of the two lines above you are already running —
the same `$NAME` the `-n` flag carries, so the herdr agent, the ledger row and the
remote card all read one string:

```bash
herdr agent start "$NAME" --kind claude --pane "$L1" -- -n "$NAME" --session-id "$SID" --remote-control "$NAME"
```

```bash
herdr agent start "$NAME" --kind claude --pane "$L1" -- -n "$NAME" --session-id "$SID" --permission-mode manual --remote-control "$NAME"
```

**Opt-in is forced, not stylistic.** `--remote-control` HARD-EXITS before the
session starts on an account whose organization disables Remote Control, or whose
subscription does not cover it. Sending it unconditionally would brick every spawn
for those operators — so a caller who did not ask for a bridge gets a launch line
byte-identical to the ones above it, with no remote-control argument anywhere.

**Naming is not bridging.** `-n` is present on every launch and is what `peer.py`
and the ledger resolve against; the bridge is the separate argument. A caller that
declines the bridge still names its worker.

**A duplicate-name refusal here is exit 1, and exit 1 is a hard stop.** herdr
carries `agent_name_taken` — *"agent name <N> is already used"* — so `agent start`
will not silently attach to somebody else's agent, which is the good news. The trap
is on your side: `SKILL.md` tells you a denied call should be reissued up to three
times, and that rule is about the **auto-mode classifier**, which rejects a call
before the shell ever runs it. §0 of this file already draws the line — exit 2 is
your syntax, exit 1 is *herdr understood you and refused*. Never retry an exit 1.

- **The `--` is mandatory and it fails loudly without it** — `unknown option: -n`,
  exit 2, nothing started. That is the good case: it is a syntax error, not a worker
  quietly coming up under a name you did not choose. If you see exit 2 from this
  command, you dropped the separator.
- **Use one string for both names.** herdr's agent name must match
  `[a-z][a-z0-9_-]{0,31}` and be unique among live agents; `claude -n` is looser.
  Passing the same `$NAME` to both keeps the single join key `SKILL.md` is built on.
- `--kind` accepts `claude` among 21 kinds; `herdr agent` lists them.
- The pane must be at an interactive shell prompt with nothing in the foreground.
  A tab you just created is.

**Verify where it landed rather than trusting `--cwd`**, for the same reason cmux
verifies its `cd` — the flag is easy to omit and nothing else catches it:

```bash
python3 "$O" "$LEDGER" "$NAME" cwd        # must be $REPO
```

Compare as paths, not strings: on macOS `$TMPDIR` carries a trailing slash and `/var`
is a symlink to `/private/var`, so a healthy run prints two visibly different strings.
`os.path.samefile` is the comparison.

### `agent start` says "ready" when the worker has not started at all

This is the sharpest trap on this host. Measured twice on 2026-08-12, in two
different directories:

```
"agent_status":"idle", "interactive_ready":true    exit 0, 3.0 s
```

…returned while the worker sat on the folder-trust gate, with **no registry entry at
all** (`peer.py` exit 1). herdr is classifying the pane, and a claude that has not
finished starting looks idle to a screen classifier.

Two signals tell the cases apart, and only the first is authoritative:

| | `peer.py "$NAME" name` | `terminal_title` |
| --- | --- | --- |
| parked on a gate | exit 1 | the raw argv — `claude -n probe-block --permission-mode manual` |
| really running | exit 0 | `✳ probe-block` |

So **run `SKILL.md`'s readiness loop on this host too, and do not shorten it because
`agent start` blocked for you.** In the healthy case it costs nothing — measured
`addressable after 0s`, because `agent start` really had waited. In the gated case it
is the only thing standing between you and the next paragraph.

### Never send the task until the registry answers

Measured, on a worker still parked on the gate:

```
herdr agent prompt <name> "<the task>" --wait   →  success, "agent_status":"done", exit 0
```

and all three of these happened:

1. the trailing Enter selected **"1. Yes, I trust this folder"**, granting read, edit
   and execute in a directory the user never approved;
2. the task text was swallowed — the prompt line was left empty;
3. the worker took **zero turns**, proved by the absence of its transcript file.

The call reported success for all of it. This is why readiness is the registry and
never the host's word for it.

`esc` is the safe answer when the gate names a directory you did not intend — it
cancels and exits `claude` cleanly, without trusting anything:

```bash
herdr pane send-keys "$L1" esc
```

## 5. Send a task, and run slash commands

Prose tasks go by `SendMessage`, as `SKILL.md` says. For anything that must arrive as
typed input — a slash command, a dialog answer — herdr has one atomic call:

```bash
herdr agent prompt "$NAME" "/spec-write <what to write up>" --wait --timeout 120000
```

`agent prompt` submits the text and an encoded Enter together, honouring the pane's
live bracketed-paste mode, so there is no half-typed intermediate state and no second
call to forget. **Slash commands do expand through it** — verified by sending
`/context`, which rendered the real context breakdown rather than arriving as literal
text.

`--wait` waits for the first settled `idle`, `done` or `blocked`. A prompt sent from a
non-working state must produce an observed lifecycle change within five seconds or
herdr returns `agent_prompt_stalled` rather than hanging.

**`agent prompt` delivers work, so it needs the same ownership check `SendMessage`
gets — and the shipped `PreToolUse` hook does not give it one.** That hook matches
`SendMessage`; this is a `Bash` call, and nothing intercepts it. The exposure here is
sharper than cmux's, because this command is addressed **by bare name** — the one
identifier `SKILL.md` spends a section explaining is not an identity: freed when a
session exits, reclaimable by anyone, auto-assigned to every hand-started session, and
unique only among the live ones at a single instant. herdr's own agent namespace is
also separate from the peer registry, so a name free in one can be taken in the other.
So `herdr agent prompt "$NAME" …` will type a whole task into whatever currently
answers to that name, with nothing anywhere reporting a fault.

The occupant check below is what closes it, and **it is documented after this command
rather than before it** — do not read that order as the running order. Run it first,
or this is the channel by which a stale name hands the user's own session your work.

### The occupant check — is our worker still the one in that pane

`SKILL.md`'s "A slot is not a session" applies here in full: a pane outlives the
agent in it, so a worker that exited leaves a pane the user can start their own
`claude` in, and `$L1` keeps resolving.

**herdr reaches the same evidence cmux does, and this file claimed otherwise until
2026-08-17.** `PaneInfo` carries no tty — that part was right — but
`herdr pane process-info` carries the pane's `shell_pid`, and `ps` turns a pid into a
tty. So `SKILL.md`'s occupant check is not a cmux facility that herdr has to
approximate with weaker witnesses. It is host-blind by design, it takes a tty as its
argument, and the two lines below are what hand it one.

Run all three checks. They answer different questions and none of them substitutes
for another.

**1. The pane is the pane the row was written about.** Both herdr witnesses come from
one call:

```bash
herdr pane get "$l1" | python3 -c '
import json,sys
p=json.load(sys.stdin)["result"]["pane"]
print("terminal_id", p["terminal_id"])                      # must equal ledger column 3
print("agent_session", (p.get("agent_session") or {}).get("value",""))'
```

`terminal_id` is minted per terminal and never reused, so a mismatch means this
`w9:p3` is not the `w9:p3` the row was written about — exactly what §2 keeps column 3
for. **A mismatch here is an unconditional stop.**

**Read both from `pane get`, not from `agent get`.** `pane get` carries
`agent_session` in the identical shape — measured 2026-08-17 — and it is addressed by
column 2 of your own ledger instead of by a name, which §7 spends a section
establishing is mutable third-party state. A check that looks its own subject up by
name has a hole in it the size of `herdr agent rename`.

If you do reach for `agent get`, the accessor is **`["result"]["agent"]`, not
`["result"]`** — it wraps exactly as `pane get` does,
`{"result":{"agent":{…,"agent_session":{…}},"type":"agent_info"}}`, and the shortened
accessor returns an **empty string rather than raising**, because the lookup is a
chained `.get`. That is worse than the `KeyError` §9 records: it fabricates the same
empty value a gated worker legitimately has. Caught that way on 2026-08-13, against a
live worker whose `agent_session.value` did match.

**2. The `claude` in there is ours.** This is the only one of the three that can say
so, and it is the one that works before the worker has registered anything:

```bash
OC="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/occupant.py"
SHPID=$(herdr pane process-info --pane "$l1" | python3 -c '
import json,sys
print(json.load(sys.stdin)["result"]["process_info"]["shell_pid"])')
TTY=$(ps -o tty= -p "$SHPID" | tr -d " ")
[ -n "$TTY" ] || { echo "no tty behind $l1 -- do not send, key, or close it" >&2; exit 1; }
python3 "$OC" "$TTY" "$LEDGER" "$NAME" || exit 1        # 0 is the only pass
```

That block is the one place in this file that must be **a single `Bash` call**:
`SHPID` and `TTY` are derived, and §1 records that a call's shell state does not
outlive it. `SKILL.md` has the exit table — 0 proceeds, 3 and 4 are both hard stops,
and you must not test for 3 in particular.

**`shell_pid`, not the foreground pid, and that is deliberate.** The shell is in the
pane whether or not a `claude` is, so this resolves a tty for an *empty* pane too —
and an empty pane is `occupant.py`'s exit 0, the case that keeps a finished worker's
tab from leaking at teardown. Reading the foreground process instead would leave you
with no tty in exactly the case §9 needs one.

**3. `agent_session.value` corroborates — and only when it holds something.**

| what it holds | what it means | what to do |
| --- | --- | --- |
| equal to ledger column 5 | herdr saw our worker register | corroboration — proceed |
| non-empty and unequal | another session reported itself into our pane | **stop** |
| empty, or the key absent | nothing has been reported yet | **not a mismatch** — proceed on checks 1 and 2 |

**That last row is the fix, and the row above it in this section is why it is safe.**
`agent_session` is written by `pane.report_agent_session`, which a `claude` still on
the folder-trust dialog has not reached. Measured 2026-08-17, one worker across the
gate:

| | parked on the gate | 1 s after `enter` |
| --- | --- | --- |
| `agent_session` in `pane get` | **key absent** | `{"agent":"claude","kind":"id","source":"herdr:claude","value":"<the minted uuid>"}` |
| the chained `.get` above | `""` | the minted uuid |
| `terminal_id` | matched column 3 | matched column 3 |
| `terminal_title` | the raw typed argv | `✳ <name>` |
| `occupant.py` | **0** | 0 |

So the unqualified **"stop on either mismatch"** this section used to end with stopped
the run on its own gated worker — forbidding the `enter` that was the only thing that
would ever populate the witness. Same shape as the cmux defect v0.9.2 fixed, reached
by a different route, and found the same way: by the smoke test, on 2026-08-17.

**Scoping it to "non-empty" is safe only because check 2 exists.** On its own,
"empty means proceed" reopens the exact hole this section is for, in a narrower
window. Constructed and measured on 2026-08-17 — a worker was launched into `wB:pS`,
exited with `esc`, the pane survived, and a `claude` was then started in it the way a
user would:

| witness | reading | the verdict it gives |
| --- | --- | --- |
| `terminal_id` | `term_659411d125b1f35` — **still matching column 3** | proceed |
| `agent_session.value` | `""` — the stranger has not registered either | proceed, under "empty means proceed" |
| `occupant.py` | **3** — the `claude` on that tty carries a different `--session-id` | **stop** |

Both herdr witnesses said proceed. Only the tty join said stop. Never relax the weak
witness in a run that has not made check 2.

**Do not reach for `terminal_title` as the argv join.** §4 records that a gated
worker's title is its raw argv, which makes it look like a way to read the minted id
without `ps`. Measured 2026-08-17, the title is capped at **73 characters**, and what
overflows is dropped from the **middle**:

| typed command | length | resulting title | carries the uuid |
| --- | --- | --- | --- |
| `claude -n witness-probe --session-id <uuid> --permission-mode manual` | 97 | `claude -n witness-probe --session-id <uuid>` | yes — by luck; the uuid ends at character 73 exactly |
| `claude -n witness-probe-two-longer --session-id <uuid> --permission-mode manual` | 109 | `claude -n witness-probe-two-longer --session-id  --permission-mode manual` | **no — the uuid alone was elided and both ends kept** |

Eleven more characters of worker name is all it takes: the flag stays, its value
silently empties, and a title-based join reads a healthy worker as a stranger. The
title is also the **typed** command rather than the process argv — the real argv on
this machine runs past 2000 characters because a wrapper injects a `--settings` blob
the title never shows. `process-info` returns argv as a structured array with no cap
at all, which is what `occupant.py` reads through `ps`.

**What check 2 actually compares is the uuid, not the presence of the flag.** Measured
2026-08-17: on this machine the wrapper injects a freshly generated `--session-id`
into a *hand-started* `claude` too, so "a human never passes that flag" is not
literally true here. It does not weaken the join — an injected uuid is random and
never equals the one this run minted, which is why the stranger above scored 3 — but
never rewrite the comparison as "does its argv mention `--session-id`".

And note what none of the three can tell you: a human who types `claude` in a herdr
pane **self-registers with herdr** through the user-scope `SessionStart` hook, so
their session appears in `herdr agent list` looking exactly like a worker. The ledger
is what distinguishes them — which is precisely why check 2 joins on the uuid *this
run minted* rather than on anything herdr reports.

## 6. Read a worker's screen

```bash
herdr agent read "$NAME" --source recent-unwrapped --lines 200
herdr pane read  "$L1"   --source recent-unwrapped --lines 200
```

Sources: `visible` is the rendered viewport, `recent` is recent output including soft
wraps, `recent-unwrapped` joins soft wraps (prefer it for logs and transcripts), and
`detection` is the plain-text snapshot herdr's own agent classifier reads. Add
`--format ansi` only when colour is the evidence.

**`visible` silently saturates; `recent-unwrapped` does not — but it is only
available while the worker is idle.** Both halves were measured against a worker that
had emitted 120 marked lines.

Idle:

| source | `--lines 40` | `--lines 200` | `--lines 600` |
| --- | --- | --- | --- |
| `visible` | 32 | 46 | 46 |
| `recent` / `recent-unwrapped` | 32 | **121** | 121 |

`visible` stops at the viewport however large `--lines` is, and it stops *without
saying so* — which is exactly how a check for "did this worker's first reply get
refused" returns a clean `0` having never looked at the line in question.

Working:

```
{"error":{"code":"agent_not_idle","message":"cannot read 60 lines while e2e-smoke is
 working: its alternate-screen history can only be captured by scrolling while idle.
 Wait and retry, or use --source visible"}}
```

Claude Code runs on the **alternate screen**, and herdr recovers its history by
scrolling that screen — which it can only do when the agent is not actively drawing to
it. So the two sources are not interchangeable and neither is a superset:

- **while working** — only `visible` answers. It is the live viewport and it is how you
  watch a run in progress or identify an open dialog.
- **while idle** — `recent-unwrapped` with a generous `--lines` is the only thing that
  answers a question about *history*, and it is a hard error, never a short answer, if
  you ask too early. That error is a good citizen: it names the condition and the
  workaround rather than silently truncating.

Anything that greps a worker's transcript — did its first reply get refused, what did
it print before it stalled — must therefore wait for idle. If a read still saturates
once idle, the documented fallback is to ask the worker to write its answer to a file
and read the file.

## 7. Keystrokes

```bash
herdr agent send-keys "$NAME" enter
herdr agent send-keys "$NAME" esc
herdr pane  send-keys "$L1"   enter     # before the agent is registered
```

**Prefer the pane form throughout, not just before registration.** `$L1` is column 2
of your own ledger row; `"$NAME"` is a lookup in a registry the whole machine writes
to. The two are equivalent only while nothing has changed, and the pane form is the
one that cannot drift.

**A name here is not evidence of ownership, in either direction.** Measured
2026-08-13:

- **A human who types `claude` in a herdr pane self-registers with herdr.** The
  integration is a `SessionStart` hook installed at *user* scope, gated only on
  "am I in a herdr pane" — never on "did herdr start me". So hand-started sessions
  appear in `herdr agent list` looking exactly like spawned workers.
- **A name is released the moment its agent exits** and is then claimable by
  anyone, and `herdr agent rename <target> <name>` re-points a name onto any live
  agent with no ownership check at all. A name is mutable third-party state.
- `agent_session.value` — herdr's own record of the Claude session id — is set by
  `pane.report_agent_session`, which any process running as the user may call for
  any pane. It is an accident-detector, never a boundary.

So resolve through `owned.py` and your ledger, and use `$L1` as the target. Use the
agent form only where herdr offers no pane equivalent.

Use the pane form while the worker is still pre-registration (a gate) as well.
herdr validates every key before writing any bytes. One `send-keys` per `Bash` call.

## 8. Wait for a worker to block — herdr's one genuine advantage

```bash
herdr agent wait "$NAME" --until blocked --timeout 120000
```

herdr classifies the agent in each pane as `idle`, `working`, `blocked`, `done` or
`unknown`, and this is a real blocking wait rather than a poll. Measured: a
`--permission-mode manual` worker told to run `ls /usr/share/dict` reached `blocked`
in 3.9 s, and the registry agreed exactly — `status=waiting`,
`waitingFor=permission prompt`, same worker, same moment. `herdr agent send-keys
<name> enter` cleared it and both signals moved on together.

`done` is the same underlying idle state as `idle` after unseen background work
finished; focusing a tab marks it seen, and **CLI reads do not**. `unknown` means an
agent is present but herdr cannot classify it — it does not prove completion, and it
does not prove death.

**So `--until idle` is the wrong way to wait for a finished worker.** Measured
2026-08-12: `herdr agent wait <name> --until idle --timeout 90000` timed out with
`{"error":{"code":"timeout"}}` on a worker that had *already* finished, because a
worker whose tab you never focused settles as `done`, and `done` does not satisfy
`--until idle`. Since a worker spawned `--no-focus` is by construction one whose tab
nobody focused, that is the normal case here, not an edge one. Wait on `done`, wait on
both, or omit `--until` entirely — bare `agent wait` uses the settled-state defaults
and accepts all three. To block on "finished", prefer the watcher's `DONE` — or its
other rendering `GATE`, the same transition for a worker that ended its turn on a
question — which is driven by the registry rather than by whether a human looked at a
tab. herdr's own `done` cannot tell those two apart either: a worker sitting on a prose
gate has ended its turn, so the pane settles exactly as a finished one does.

**Use this in addition to the watcher, never instead of it** (`SKILL.md`, "A host
that publishes its own agent states does not replace this"). Nothing here reports a
worker that died; only the registry pid check does.

`herdr agent list` and `herdr agent get <name>` give the same states without waiting,
and `herdr tab list --workspace "$WS"` aggregates a status per tab — with one tab per
worker that is a per-worker status line for free.

## 9. Close what the run opened

The ledger stores the worker's **pane** id, so derive its tab at teardown rather than
adding a column for it:

```bash
TAB=$(herdr pane get "$l1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["tab_id"])')
```

**`["result"]["pane"]`, not `["result"]`.** `pane get` wraps its payload:
`{"result":{"pane":{…,"tab_id":"w9:t8"},"type":"pane_info"}}`. The shortened accessor
raises `KeyError: 'tab_id'` — caught by a live smoke run on 2026-08-12, where it threw
on the **first** step of teardown and would have left the worker tab open and its
ledger row unpruned. `pane current` wraps the same way. Read the shape rather than
predicting it; that is herdr's own advice and this is what ignoring it costs.

That command is also the liveness check, and **`pane_not_found` on its own is not
proof the user closed anything.** It answers identically for a pane that is genuinely
gone, for a pane id that did not survive a herdr **server restart** (§2 records that
durability as unmeasured, and the ledger outlives a restart because it is a file), for
the corrupted row `SKILL.md` records under "Resolve, guard, then write", and — before
column 7 — for a cmux locator handed to it by a cross-host run. Ask the registry, which
is host-independent, before you believe it:

```bash
reg=$(python3 "$O" "$LEDGER" "$name" status)
herdr pane get "$l1" >/dev/null 2>&1 || [ -z "$reg" ] || {
  echo "$name: tagged herdr, pane_not_found for $l1, and the registry still answers '$reg' -- a broken row or a restarted server, not a finished worker. Not closing, not pruning." >&2
  exit 1; }
```

Only when both agree — the pane is gone *and* the worker no longer answers — is this
the case where you close nothing and prune the row. The wrong version of this sentence
is worse here than on cmux: a cmux supervisor reading a herdr row merely leaked the
tab, whereas a herdr supervisor reading a cmux row leaked the surface **and pruned the
only record that it exists**, since "drop the row" is the prune spelled out.

**This close *is* reachable from a supervisor that is not in herdr**, and that is why
`SKILL.md`'s teardown table treats the two hosts differently rather than refusing both.
`herdr tab close <tab>` takes an explicit id and reads nothing from the environment —
no workspace flag, no `--current`, no fallback to a focused pane — and the tab id is
derived from the row's own column 2 by the command above. So a cmux supervisor holding
a row whose column 7 says `herdr` closes it by reading this file. **Read the whole of
it, §5's occupant check included.** That check is the only thing standing between this
command and a pane the user has since taken over, and it lives in this file rather than
in `SKILL.md` precisely because it is host-shaped: both hosts join on a tty, but each
names one its own way, and herdr's does not come from `PaneInfo` — it takes the extra
`pane process-info` hop §5 spells out. A supervisor that arrives here with the close
half and not the occupant half is one context summarization away from killing what the
user started.

```bash
herdr tab close  "$TAB"     # the whole worker tab -- the tidy default here
herdr pane close "$l1"      # when the worker was a split inside a shared tab
```

Both return `{"type":"ok"}` on success. **There is no misleading echo to misread** —
unlike cmux's `OK surface:N`, herdr tells you nothing about ids here, so the only
confirmation is re-resolving:

```bash
herdr pane get "$L1"        # exit 1 + pane_not_found once it is really gone
```

Closing a tab kills the worker in it; the registry then reports it gone on the pid
check, and the run's watcher emits its `GONE` — expected and benign if you closed it
deliberately.

Never close a tab or pane that is not in this run's ledger. Never close
`$HERDR_PANE_ID`, your own. Never `herdr workspace close` for tidying, and **never
`herdr server stop`** — herdr's own guidance flags it as never-run-from-an-active-
session, because it kills every pane process the server owns, including the user's.

## 10. Where you are

```bash
herdr pane current --current                     # the calling pane, not the focused one
herdr pane list --workspace "$HERDR_WORKSPACE_ID"
herdr tab  list --workspace "$HERDR_WORKSPACE_ID"
herdr pane layout --pane "$HERDR_PANE_ID"        # which way to split, if splitting
```

`--current` is the point of that first line: without it, `pane current` returns
whatever pane is focused, which may belong to the user or to another client.
