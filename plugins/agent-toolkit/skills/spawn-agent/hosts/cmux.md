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

Column 3 is not decoration: it is how a run finds the pane it already owns, so the
whole run costs one split.

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
  $(awk -F'\t' -v c=3 'NF>=3 && $c!=""{a[++n]=$c} END{for(i=n;i>0;i--) if(!seen[a[i]]++) print a[i]}' "$LEDGER" 2>/dev/null))

if [ -n "$PANE" ]; then          # tab into the pane this run already owns
  SURF=$(cmux new-surface --workspace "$WS" --pane "$PANE" --type terminal --focus false \
           | grep -o 'surface:[0-9]*')
else                             # first agent of the run: one split, anchored on YOU
  SURF=$(cmux new-split right --workspace "$WS" --surface "$CMUX_SURFACE_ID" --focus false \
           | grep -o 'surface:[0-9]*')
fi
[ -n "$SURF" ] || { echo "no surface came back; not sending anything" >&2; exit 1; }
```

`-v c=3` rather than a dollar sign followed by the digit 3, for the reason `SKILL.md`
gives under "The ledger" — a bare dollar-plus-digit in skill text is replaced by the
skill's own invocation arguments before you read it, so the literal form silently scans
the wrong column.

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

That is the live viewport, and on this host it is **all you can ever have**. Use it for
what a viewport answers — is a dialog open, which row carries the `❯`, did the launch
leave a shell prompt with an error above it — and for nothing that asks about history.

**`--scrollback` and `--lines` do not reach a Claude Code worker's history, and they
fail by returning the viewport rather than by erroring.** Measured 2026-08-12 against a
worker with 120 uniquely marked lines and one real refusal behind it: plain,
`--scrollback`, `--lines 40`, `--lines 200`, `--lines 600`, `--lines 2000` and
`capture-pane` alike all returned **the same 55 viewport lines**, reaching 47 of the 120
markers. `--lines` implies `--scrollback`; they are one control, not two. The flags are
not broken — a control run in the same surface, after `/exit` dropped it to a plain
shell, returned 128 lines and every one of the 120 markers on the first try. The cause
is that Claude Code runs on the **alternate screen**, which has no scrollback for cmux
to read. herdr recovers alt-screen history by scrolling it while the agent is idle;
cmux exposes no command that does, so there is no flag and no retry that fixes this.

So **never answer a history question from this host's screen** — "did its first reply
get refused", "what did it print before it stalled". A grep of those 55 lines returns a
clean zero over a refusal sitting just above them, which is a false green and not a
short answer. Read the worker's transcript instead (`SKILL.md`, "Read a worker's
output"): count `SendMessage` `tool_use` blocks and read refusals out of the matching
`tool_result` blocks. That file has neither a viewport nor a host in it.

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
[ -n "$ref" ] || { echo "already gone; closing nothing" >&2; exit 0; }
[ "$(python3 "$S" "$l1" id)" != "$CMUX_SURFACE_ID" ] || { echo "that is MY surface" >&2; exit 1; }
python3 "$OC" "$(python3 "$S" "$l1" tty)" "$LEDGER" "$name" || {
  echo "somebody else is working in that tab now; leaving it open" >&2; exit 1; }
cmux close-surface --workspace "$WS" --surface "$ref"
```

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
