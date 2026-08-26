# Host — cmux

The commands `spawn-agent` needs when `$CMUX_SURFACE_ID` is set and `HERDR_ENV` is
not. Read this alongside `SKILL.md`, not instead of it: everything about the peer
registry, messaging, the watcher, permission classes and the ledger lives there and
is not repeated here.

**One command per `Bash` call here too.** The blocks below are grouped for reading.

## 1. Bind the caller's slot

A **pane** is a split; a **surface** is a tab inside a pane. The slot is the
surface — the tab this session occupies.

```bash
[ -n "$CMUX_WORKSPACE_ID" ] && [ -n "$CMUX_SURFACE_ID" ] || {
  echo "not in a cmux terminal (need CMUX_WORKSPACE_ID and CMUX_SURFACE_ID); not spawning" >&2
  exit 1
}
WS="$CMUX_WORKSPACE_ID"
CALLER_SLOT="$CMUX_SURFACE_ID"
```

Both ids, not one: every placement below passes `--surface "$CMUX_SURFACE_ID"`, and
the ledger is keyed by it.

**Repeat those two lines at the top of every later `Bash` call that needs them, and do
not try to `export` your way out of it.** A `Bash` call's shell state does not outlive
the call — measured 2026-08-12, an exported variable read back empty both in the next
`Bash` call and inside a `Monitor` command. `$CMUX_SURFACE_ID` and `$CMUX_WORKSPACE_ID`
survive because they are in the process environment, which is exactly why the two names
above are derived from them rather than carried forward. For the `Monitor` command,
write the expanded slot id in literally.

A cmux surface uuid satisfies the four slot properties in `SKILL.md` directly: it is
globally unique, it outlives any one `claude` process, and it is already
filename-safe.

The resolver for surfaces:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux-surface.py"
```

`cmux-surface.py <ref|uuid> <field>` resolves both directions — `ref` → `id`, `id` →
`ref` — plus `pane_ref`, `pane_id`, `tty` and `title`, and prints nothing (exit 1)
once that surface is gone. Re-paste that assignment in every `Bash` call that uses
`$S`; variables die with the shell.

## 2. Ledger locators

| column | holds | resolve with |
| --- | --- | --- |
| 2 | the worker's **surface uuid** | `python3 "$S" "$l1" ref` — empty/exit 1 means the surface is gone |
| 3 | the worker's **pane uuid** | `python3 "$S" "$SURF" pane_id` |
| 7 | the literal `cmux` | not resolved — `SKILL.md`'s setup block writes it from §0's detection. Never typed here |

Column 3 is not decoration: it is how a run finds the pane it already owns, so the
whole run costs one split.

**Rows 2 and 3 mean what this table says only for a row whose column 7 is `cmux`.** A
row tagged otherwise holds another host's ids, and the failure is not an error — see
§7, where this host's resolver turns a foreign locator into a clean "nothing to do".
Nothing in this file binds column 7: it is derived once, in `SKILL.md`'s setup block,
from the same expression §0 used to send you here, so that the tag and the choice of
this file are a single decision rather than two that can disagree.

## 3. Where a worker goes

Every split shrinks what the user is already reading, so a whole run adds at most
one:

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
  $(awk -F'\t' -v c=3 -v k=7 -v h=cmux 'NF>=7 && $c!="" && $k==h {a[++n]=$c} END{for(i=n;i>0;i--) if(!seen[a[i]]++) print a[i]}' "$LEDGER" 2>/dev/null))

if [ -n "$PANE" ]; then          # tab into the pane this run already owns
  SURF=$(cmux new-surface --workspace "$WS" --pane "$PANE" --type terminal --focus false \
           | grep -o 'surface:[0-9]*')
else                             # first agent of the run: one split, anchored on YOU
  SURF=$(cmux new-split right --workspace "$WS" --surface "$CMUX_SURFACE_ID" --focus false \
           | grep -o 'surface:[0-9]*')
fi
[ -n "$SURF" ] || { echo "no surface came back; not sending anything" >&2; exit 1; }
```

`-v c=3`, `-v k=7` and `-v h=cmux` rather than dollar signs followed by digits, for the
reason `SKILL.md` gives under "The ledger" — a bare dollar-plus-digit in skill text is
replaced by the skill's own invocation arguments before you read it, so the literal
form silently scans the wrong column. Three `-v` flags on one line is dense; a bare
seven would be replaced by this skill's seventh argument.

**The `$k==h` term is not fixing a live bug, and that is the reason to add it now.**
Today the candidates are intersected against real `list-panes` output, and a herdr
`term_…` string is never a live cmux pane uuid — verified on a mixed ledger, where the
filtered form emits only the two cmux pane uuids while the unfiltered form emits
`term_x` between them. So the guard is currently the intersection, which is a fact
about two projects neither of which is this one, holding up a scan that is one refactor
away from having nothing else. `NF>=7` also means a legacy ledger yields no candidate
at all, so the run splits once more than it needed to — the correct failure direction,
and unreachable anyway once the setup block refuses a six-column file.

herdr has no equivalent scan and needs none: it creates a fresh tab per worker and
reads the ledger nowhere. This is the only place in the plugin where one host's rows
could reach another host's resolver by a *scan* rather than at teardown.

Then resolve both locators for the ledger row (`SKILL.md`, "Spawn one agent", step 2):

```bash
L1=$(python3 "$S" "$SURF" id)
L2=$(python3 "$S" "$SURF" pane_id)
```

A new surface lands **after the selected tab**, not at the end, so never infer which
worker is which from tab order. The tab title is the name (`⠂ audit-api`), which is
the only thing that distinguishes four identical terminals.

## 4. Launch

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd \"$REPO\" && claude -n $NAME --session-id $SID\n"
```

With a permission class, on the same line:

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd \"$REPO\" && claude -n $NAME --session-id $SID --permission-mode manual\n"
```

### Remote control — opt-in, and only when the caller asks

A caller that wants the worker reachable from a phone or claude.ai/code supplies a
remote-control name; nothing else changes. Append `--remote-control "$NAME"` to
whichever of the two lines above you are already sending — the same `$NAME` the
`-n` flag carries, so the tab title, the ledger row and the remote card all read
the same string:

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd \"$REPO\" && claude -n $NAME --session-id $SID --remote-control \"$NAME\"\n"
```

```bash
cmux send --workspace "$WS" --surface "$SURF" "cd \"$REPO\" && claude -n $NAME --session-id $SID --permission-mode manual --remote-control \"$NAME\"\n"
```

**Opt-in is forced, not stylistic.** `--remote-control` HARD-EXITS before the
session starts on an account whose organization disables Remote Control, or whose
subscription does not cover it. Sending it unconditionally would brick every spawn
for those operators — so a caller who did not ask for a bridge gets a launch line
byte-identical to the ones above it, with no remote-control argument anywhere.

**Naming is not bridging.** `-n` is present on every launch and is what keeps four
identical terminals apart; the bridge is the separate argument. A caller that
declines the bridge still names its worker.

**The inner `\"` around `$REPO` is not decoration.** The outer quotes belong to the
supervisor's shell and are consumed there; what reaches the worker's terminal is the
expanded text. A repo path with a space arrives as `cd /Users/ns/my repo && …`, and
in **zsh** — the shell the worker actually gets — two-argument `cd` is not an error
about arguments at all, it is the *string-substitution* form (`cd old new` rewrites
`$PWD`). So it fails like this:

```
$ zsh -c 'cd /path/to/my repo && echo REACHED'
zsh:cd:1: string not in pwd: /path/to/my
exit=1
```

`REACHED` never prints, the `&&` short-circuits, and **`claude` never starts at
all**. Note what that message does *not* say: nothing in `string not in pwd` points
at a space in the path, so a supervisor reading it in a worker's tab has to already
know this to decode it. `[ -d "$REPO/.git" ]` passes such a path happily, nothing
else catches it, and the only symptom upstream is the readiness loop burning its
full 60 seconds. Quote *inside* the payload, and with escaped double quotes rather
than single ones — single quotes break on an apostrophe in the path, which on macOS
is likelier than a `$`.

**`cd` is not optional, and it is easy to drop** — it sits mid-string inside a
longer `cmux send` line rather than standing on its own, so it goes missing without
anything looking wrong. It is also self-concealing: a split off your own surface
inherits *your* cwd, so if you happen to be in the right repo the omission passes
silently and only misbehaves when you aren't. A supervisor reading this exact
warning still dropped it on the very next line it wrote (2026-08-09) and got away
with it for precisely that reason — so do not treat having read the warning as
having complied with it. That is the **third** recorded occurrence of this one drop,
which is the measurement that matters here: a warning is demonstrably not a control.
**Verify instead of trusting:**

```bash
python3 "$O" "$LEDGER" "$NAME" cwd        # must print $REPO
```

**And the verification is self-concealing in the same way the bug is.** It has power
only when `$REPO` differs from the caller's own cwd. On the third occurrence it
*passed* — the worker really was in `$REPO`, because a new split inherits the
caller's cwd and the caller was already there. When the two are the same, this check
cannot fail, so it proves nothing; read the launch string itself, before you send it,
and confirm the `cd` is in it.

A new terminal inherits the cwd of the pane it came from, not the workspace's
directory — split off the caller and you get the caller's cwd; split off something
else and you get *its* cwd (a probe launched from one repo came up in an unrelated
scratch directory that way). Never rely on either.

`new-surface` takes `--working-directory "$REPO"` and it works (verified
2026-08-09: a surface opened that way came up in the named directory). Pass it as
a second layer where you can — but **it does not retire the `cd`**, because
`new-split` has no such flag, so the first agent of every run is placed by the one
command that cannot be told where to land.

`cmux new-surface --type agent-session --provider claude` also opens a Claude
surface, but it takes no `-n`, so the worker comes up under a derived name you did
not choose — which is the join key. Spawn terminals.

## 5. Read a worker's screen

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux-surface.py"
REF=$(python3 "$S" "$l1" ref)
[ -n "$REF" ] || { echo "that surface is gone" >&2; exit 1; }
cmux read-screen --workspace "$WS" --surface "$REF"
```

**Resolve `$l1` here rather than reusing `$SURF`.** `$SURF` was assigned in the
placement call, in a different `Bash` call, and shell state does not survive one —
so by the time you read a screen it is empty, and an empty `--surface` is the
caller's own (see §6). A read is the least destructive of the four, which is exactly
why it is the easiest place to acquire the habit of reading your own screen back and
believing it is the worker's.

That is the live viewport. Use it for what a viewport answers — is a dialog open, which
row carries the `❯`, did the launch leave a shell prompt with an error above it.

**The read flags cannot give you anything but that viewport — but the keystroke channel
can move which part of the alternate screen the viewport shows.** Those are two separate
facts, measured two weeks apart, and the rest of this section keeps them apart: the
first is why the transcript is the default, the second is what closes the one case the
transcript cannot answer.

**`--scrollback` and `--lines` do not reach a Claude Code worker's history, and they
fail by returning the viewport rather than by erroring.** Measured 2026-08-12 against a
worker with 120 uniquely marked lines and one real refusal behind it: plain,
`--scrollback`, `--lines 40`, `--lines 200`, `--lines 600`, `--lines 2000` and
`capture-pane` alike all returned **the same 55 viewport lines**, reaching 47 of the 120
markers. `--lines` implies `--scrollback`; they are one control, not two. The flags are
not broken — a control run in the same surface, after `/exit` dropped it to a plain
shell, returned 128 lines and every one of the 120 markers on the first try. The cause
is that Claude Code runs on the **alternate screen**, which cmux's *reader* has no
scrollback to walk. Re-confirmed 2026-08-27 on a worker with 150 marked lines behind it:
plain and `--scrollback --lines 200` both returned the same 52 viewport lines and
reached **0** of the 150 markers.

**Flag order does not matter here, and v0.10.1 of this file said it did.** That claim is
**withdrawn**: `--scrollback --lines 200` written before `--surface` and written after it
both exit 0 with empty stderr and byte-identical output — measured 2026-08-27 on Claude
Code v2.1.246, three calls including the plain form, 2679 bytes apiece, exit status
captured directly rather than through a pipeline. It is recorded rather than deleted
because it was believed, shipped, and is worth recognising if it is ever re-derived.

**What produced the error was zsh, not cmux, and *that* trap is real.** The observation
behind the retracted claim came from a loop that passed the flags through an unquoted
variable:

```bash
f="--scrollback --lines 200"
cmux read-screen --workspace "$WS" --surface "$REF" $f   # Error: unexpected arguments
```

**zsh does not word-split an unquoted parameter expansion**; bash does. So `$f` arrives
as a *single* argv word — the literal string `--scrollback --lines 200` — which cmux is
right to reject. Reproduced deliberately 2026-08-27: exit 1 with that exact message,
while the same three flags written as three words exit 0. The tell is in the message,
which names the **whole string** as one unexpected argument: read that as "one argument
that should have been three", never as a complaint about order. It applies to every
command line in this skill that is assembled in a variable — write the flags out, or use
an array; do not reorder them and conclude you fixed something.

So **prefer the worker's transcript for anything that asks what the worker produced** —
"did its first reply get refused", "what did it print before it stalled". A grep of
those 55 lines returns a clean zero over a refusal sitting just above them, which is a
false green and not a short answer. Read the worker's transcript instead (`SKILL.md`,
"Read a worker's output"): count `SendMessage` `tool_use` blocks and read refusals out
of the matching `tool_result` blocks. That file has neither a viewport nor a host in it.

**Scrolling does not soften that, because a scroll recovers what Claude Code *drew*,
not what the worker printed.** Measured 2026-08-27: a worker told to `echo` 150 uniquely
marked lines rendered them as the single collapsed line `Ran 1 shell command`, and
scrolling that session to its very top reached the startup banner without passing one
marker. They were never on the alternate screen to be recovered. So for an output
question the transcript is not the more convenient answer, it is the only one — and the
subsection below does not change that.

### Scrolling the alternate screen — what the keystroke channel reaches

**`pageup` scrolls a worker's alternate screen; `pagedown` comes back.** Measured
2026-08-27 on this machine. It is a keystroke, so it takes **§6's whole gate** —
resolve the ref into a variable, refuse an empty one, and run the occupant check:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux-surface.py"
OC="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/occupant.py"
REF=$(python3 "$S" "$l1" ref)
[ -n "$REF" ] || { echo "that worker's surface is gone; not sending keys" >&2; exit 1; }
python3 "$OC" "$(python3 "$S" "$l1" tty)" "$LEDGER" "$NAME" || exit 1
cmux send-key --workspace "$WS" --surface "$REF" pageup
```

An empty `--surface` is your own surface here exactly as it is everywhere else in this
file, and a `pageup` that falls back to that default scrolls the session the user is
reading.

**The case this closes is the one the transcript cannot.** A long queued peer message
pushes an **open dialog** off the top of the viewport, and the screen read then shows
the queued text and no dialog at all. Reproduced 2026-08-27: a worker registered
`waiting` / `input needed` while its 53-line screen carried fifty lines of queued
message, with no `❯` and no footer anywhere on it. One `pageup` brought back

```
❯ 1. ALPHA-KEEP
  2. BRAVO-PICK

Enter to select · ↑/↓ to navigate · Esc to cancel
```

The transcript tells you a question exists. Only the screen tells you which row carries
the `❯` right now, and `SKILL.md`'s "Answer a blocked worker" turns entirely on that.

**`fn+↓ to scroll` in the screen output is how you know you are scrolled** — present in
all 9 captures of that run taken while scrolled and absent in all 8 taken at the bottom.
Match that fragment and not the whole hint: the prefix changes, reading `Jump to bottom:`
normally and `N new messages:` once the worker has drawn something below you.

Three properties of a scrolled surface, each measured in that run and each a way to be
wrong about a worker:

- **A scroll persists, and your own next screen read inherits it.** The viewport stays
  where you put it while the worker goes on drawing — three reads spread over several
  seconds of a working worker all returned the same scrolled region, while the chrome
  around it (status line, input box) stayed live. A read taken after an un-restored
  `pageup` is a read of the past wearing the present's footer.
- **Keys land while scrolled, and landing one does not restore the view.** `down` sent
  to the dialog above while the surface was scrolled moved the `❯` from option 1 to
  option 2 and left the surface exactly where it was. So the answer goes through, and
  both the operator and your next read are left looking at history.
- **It works on a worker that is still drawing**, which is where this host differs from
  herdr rather than falling short of it: herdr scrolls the alternate screen *for* you
  and so refuses with `agent_not_idle` while the agent is working, where here the scroll
  is a keystroke and nothing arbitrates it.

**So `pagedown` back to the bottom before anything else** — before the next screen read,
before the `enter` that answers the dialog, and before you leave the tab. Confirm it by
the absence of the `fn+↓ to scroll` fragment rather than by the key having been sent.

**Read first and key second at a worker sitting on a dialog.** `pageup` selects nothing
and answers nothing, which is exactly why it is safe to aim at a blocked worker; every
key you send after it is an answer.

## 6. Keystrokes

**Resolve into a variable and refuse an empty one. Never interpolate the resolver
straight into `--surface`.**

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux-surface.py"
OC="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/occupant.py"
REF=$(python3 "$S" "$l1" ref)
[ -n "$REF" ] || { echo "that worker's surface is gone; not sending keys" >&2; exit 1; }
python3 "$OC" "$(python3 "$S" "$l1" tty)" "$LEDGER" "$NAME" || exit 1
cmux send-key --workspace "$WS" --surface "$REF" down
```

**The occupant line is not belt-and-braces on top of the empty check — it catches the
opposite failure.** `[ -n "$REF" ]` fires when the surface is *gone*. The dangerous
case is the surface that is very much alive and no longer yours: the worker exited,
the tab stayed, and the user started their own `claude` in it. `$REF` resolves
perfectly, the keys land, and they land on a human. `SKILL.md`'s "A slot is not a
session" section has the sequence; `cmux-surface.py <uuid> tty` is what makes it
checkable here.

**An empty `--surface` is not a no-op — it is your own surface.** `cmux send-key
--help` documents `--surface <id|ref|index>   Target surface (default:
$CMUX_SURFACE_ID)`, and an empty value takes that default just as an absent one
does. Confirmed on the identically-documented sibling: `cmux read-screen --surface
""` exited 0 and printed **the calling session's own screen**, no error.

And empty is the resolver's *documented normal output*: `cmux-surface.py` prints
nothing and exits 1 once a surface is gone (§2 above). So the inline form
`--surface "$(python3 "$S" "$l1" ref)"` types `down` and then `enter` **into the
supervisor's own Claude Code session** the moment the worker's tab is closed or its
ledger locator is stale — arrow-down and Enter onto whatever dialog the user is
looking at. Command substitution discards the exit status, so nothing anywhere
reports a fault.

This is the same failure §3 already warns about for `new-surface`'s flags — "a
shell variable which came out blank hands the command an empty value and lets it
fall back to its own default" — reached through a different command. **Every
`--surface` and `--pane` in this file needs the value assigned and checked first.**

One `send-key` per `Bash` call — the auto-mode classifier denies the compound form
and sometimes the single form too; the single form succeeds on retry.

**Navigation keys are keystrokes and take this whole gate too.** `pageup` and `pagedown`
answer nothing and select nothing, but they are sent down the same channel to the same
resolved ref, so an empty `--surface` scrolls your own session and a stale locator
scrolls a stranger's. §5's scrolling subsection is where they are measured and where the
rule about putting the view back lives.

For a slash command, text then Enter, as two calls:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/hosts/cmux-surface.py"
OC="${CLAUDE_PLUGIN_ROOT}/skills/spawn-agent/lib/occupant.py"
REF=$(python3 "$S" "$l1" ref)
[ -n "$REF" ] || { echo "that worker's surface is gone" >&2; exit 1; }
python3 "$OC" "$(python3 "$S" "$l1" tty)" "$LEDGER" "$NAME" || exit 1
cmux send --workspace "$WS" --surface "$REF" "/spec-write <what to write up>"
```
```bash
cmux send-key --workspace "$WS" --surface "$REF" enter
```

**`cmux send` delivers work, so it needs the same ownership check `SendMessage`
gets — and it will not get one for free.** A `PreToolUse` hook on `SendMessage` does
not see this: it is a `Bash` call. So this is the channel by which a stale locator
types a whole task into a session the run never started, with nothing anywhere
reporting a fault. Guard it here or it is unguarded.

`cmux send` is unaffected by the permission-class hold that stops messaging between
classes.

## 7. Close what the run opened

```bash
ref=$(python3 "$S" "$l1" ref)
reg=$(python3 "$O" "$LEDGER" "$name" status)
[ -n "$ref" ] || [ -z "$reg" ] || {
  echo "$name: tagged cmux, its surface will not resolve, and the registry still answers '$reg' -- a broken row or a restarted cmux, not a finished worker. Not closing, not pruning." >&2
  exit 1; }
[ -n "$ref" ] || { echo "already gone; closing nothing" >&2; exit 0; }
[ "$(python3 "$S" "$l1" id)" != "$CMUX_SURFACE_ID" ] || { echo "that is MY surface" >&2; exit 1; }
python3 "$OC" "$(python3 "$S" "$l1" tty)" "$LEDGER" "$name" || {
  echo "somebody else is working in that tab now; leaving it open" >&2; exit 1; }
cmux close-surface --workspace "$WS" --surface "$ref"
```

- **An empty `ref` alone does not mean "the user closed it", and reading it that way
  is how a leaked slot gets reported as a clean finish.** `cmux-surface.py` walks the
  tree for a matching ref or id and returns 1 printing nothing when it finds none — so
  it answers *identically* for a surface the user closed, for a surface that outlived a
  cmux restart, for the corrupted row `SKILL.md` records under "Resolve, guard, then
  write", and — before column 7 — for a herdr locator handed to it by a cross-host run.
  Measured 2026-08-17: a cmux supervisor's ledger held a herdr worker's row, and this
  line is what would have consumed it, exit 0, reported as closed. The registry check
  above is what tells the cases apart: the tag says this host, the locator is dead, and
  the worker still answers — that row is broken or cmux restarted, and it is not a
  finished worker. The original `exit 0` survives underneath it, for the case that
  really is a closed tab.
- **This close is not reachable from a supervisor that is not in cmux, and that is why
  `SKILL.md`'s teardown table refuses a `cmux` row read from herdr.** Every command in
  this section passes `--workspace "$WS"`, bound from `$CMUX_WORKSPACE_ID` — which
  inside herdr is live, valid and *wrong* (`SKILL.md` §0): it names the surface
  displaying the herdr window and is identical for every herdr pane on the machine. The
  row's own locators are explicit; the ambient half is not, and a blank or wrong value
  lets the command fall back to its own default, which §3 and §6 of this file already
  document as the sharpest edge here. Report the row with its locators and its resume
  id, and leave the tab.
- **The empty-value guard is not optional here, and this is where it costs most.**
  `cmux close-surface --help`: `--surface <id|ref|index>    Surface to close
  (default: $CMUX_SURFACE_ID)`, and its summary adds *"Defaults to the focused
  surface if none specified"*. Either default is a tab this run did not spawn — your
  own, or whatever the user is reading. An empty `$ref` is the *ordinary* state on
  this path, because empty is exactly how §2's resolver says "the user already closed
  it", so the unguarded form turns "nothing to do" into "close the supervisor".
- The pane disappears on its own when its last surface closes. There is no
  `close-pane`, and nothing is left to tidy once the tabs are gone.
- **The `OK surface:N` it prints back is not the surface you closed.** The number is
  an allocation counter that has nothing to do with any live surface: measured across
  three closes it climbed 147, 148, 149, while the survivors — re-resolved from their
  UUIDs — stayed exactly where they were at `surface:145` and `surface:146`. Refs are
  *not* re-enumerated by a close. Nothing went wrong and no other tab was touched, but
  read literally the echo looks like you just closed a stranger's tab. Confirm by
  resolving the UUID (`python3 "$S" "$l1" ref` returns empty once it is gone), never
  by reading that ref.
- Closing a tab with siblings left re-selects the previously visible one and
  moves no focus. Closing the **last** one collapses the pane, and focus lands on
  the pane it was split off from — yours. Harmless, but say so when you offer, in
  case the user is reading a third pane at the time.

Never `close-others`, `close-left`, `close-right` or `close-workspace`. There is no
case in this procedure where any of them is right, and each closes tabs the run does
not own. Never close `$CMUX_SURFACE_ID`; assert that by hand before every close.

## 8. Where you are

```bash
cmux --json identify                                              # .caller is you
cmux --json --id-format both tree --workspace "$CMUX_WORKSPACE_ID" # layout, ttys, titles
```

`.caller` is where you are; `.focused` is wherever the user has drifted, and the two
routinely differ — they did while this was written (`caller` in `workspace:3`,
`focused` in `workspace:1`). Never place anything by `focused`.
