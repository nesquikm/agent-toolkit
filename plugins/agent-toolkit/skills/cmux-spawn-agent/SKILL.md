---
name: cmux-spawn-agent
description: Spawn Claude Code agents into cmux surfaces (tabs) and drive multi-stage pipelines across them. Use when the user asks to run something "in another agent", "in a new Claude Code", "in a pane", to hand work from one agent to the next ("run X to the end, then /y, then in a new session run /z"), or to run several agents in parallel and report when they finish.
---

# Spawn agents into cmux surfaces

Run work in separate Claude Code sessions that live in visible cmux surfaces, so
the user can watch each one and take it over by clicking its tab.

The move that makes a *visible* agent controllable is **assigning its session id
before launch**. That id is the join key to its status and its output.

| Need | Source |
| --- | --- |
| where you are | `cmux --json identify` → `.caller` (`.focused` is wherever the user drifted) |
| layout, ttys, titles | `cmux --json --id-format both tree --workspace "$CMUX_WORKSPACE_ID"` |
| identity + busy/idle | `claude agents --json` (keyed by the id you assigned) |
| structured output | `<config-dir>/projects/<esc-cwd>/<sessionId>.jsonl` |
| send input | `cmux send --surface <ref>` + `cmux send-key --surface <ref> enter` |
| handed-back-to-you | `cmux list-notifications --json`, matched on `surface_id` |
| finished, **pushed to you** | `cmux events --category agent`, matched on `payload.session_id` |

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

```bash
# Refuse rather than guess. BOTH ids, not just the workspace: every placement below
# passes --surface "$CMUX_SURFACE_ID", and the ledger is keyed by it.
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

# One lookup, both directions: ref -> uuid, uuid -> ref, plus pane_ref/pane_id/tty/title.
# Empty output means that surface no longer exists.
surf_field() {   # surf_field <surface ref|uuid> <field>
  cmux --json --id-format both tree --workspace "$WS" | python3 -c '
import json,sys
key,field=sys.argv[1],sys.argv[2]
def walk(o):
    if isinstance(o,dict):
        if str(o.get("ref","")).startswith("surface:") and key in (o.get("ref"),o.get("id")): return o
        return next((r for v in o.values() if (r:=walk(v))), None)
    if isinstance(o,list): return next((r for v in o if (r:=walk(v))), None)
s=walk(json.load(sys.stdin)["windows"]); print(s.get(field,"") if s else "")' "$1" "$2"
}
```

The ledger is keyed by the caller's surface, so it is exactly "the agents this
run spawned" — the only surfaces you are ever allowed to close. That key is why
the guard above refuses on an empty `$CMUX_SURFACE_ID` rather than defaulting it:
a shared `unknown.tsv` would merge two runs' ledgers, and the cleanup section
would then offer you another run's surfaces to close.

## Spawn one agent

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
PANE=$(surf_field "$SURF" pane_ref)
SURF_UUID=$(surf_field "$SURF" id)   # notifications are keyed by the UUID, never by surface:N

SID=$(uuidgen | tr 'A-Z' 'a-z')
cmux send --workspace "$WS" --surface "$SURF" "cd $REPO && claude --session-id $SID -n <name>\n"
```

**`cd` is not optional, and it is easy to drop** — it sits mid-string inside a
longer `cmux send` line rather than standing on its own, so it goes missing without
anything looking wrong. It is also self-concealing: a split off your own surface
inherits *your* cwd, so if you happen to be in the right repo the omission passes
silently and only misbehaves when you aren't. Read the launch line back before
sending it, and check `claude agents --json`'s `cwd` afterwards.

A new terminal inherits the cwd of the pane it came
from, not the workspace's directory — split off the caller and you get the
caller's cwd; split off something else and you get *its* cwd (a probe launched
from one repo came up in an unrelated scratch directory that way). Never rely on
either. `claude agents --json` reports the `cwd` the worker actually got; check
it when a worker behaves as though the repo were empty, and remember the
transcript path follows that cwd, not your intent.

Write the row down before anything else — cleanup reads nothing else, and a
surface you failed to record is one you must never touch again:

```bash
# sid, surface uuid, pane uuid, name, status
printf '%s\t%s\t%s\t%s\t%s\n' "$SID" "$(surf_field "$SURF" id)" "$(surf_field "$SURF" pane_id)" "<name>" spawned >> "$LEDGER"
```

The fifth column is what survives you. Push notifications can be missed — a
Monitor times out, a turn's context gets summarized — so flip a row to `reported`
only once you have actually told the user that worker's outcome. Then
`awk -F'\t' '$5!="reported"' "$LEDGER"` is the answer to "what am I still owed?",
and it is answerable at the start of any turn without remembering anything.

Then **wait for it to register** — that is the readiness signal, so don't guess a
startup delay:

```bash
until claude agents --json | grep -q "$SID"; do sleep 1; done
```

Only now send the task. Send the text and the Enter separately:

```bash
cmux send --workspace "$WS" --surface "$SURF" "<the full task prompt>"
cmux send-key --workspace "$WS" --surface "$SURF" enter
```

Put the **whole** spec in that first prompt — goal, constraints, and what "done"
means. These are one-shot kickoffs; a worker cannot be clarified as cheaply as a
conversation. If the user asked for `ultracode`, include that word in the prompt
text — it is a keyword the worker reads, not a CLI flag.

## Spawn several at once

One tab per worker in **the same agents pane** — a fan-out is not a reason for a
second split, and reusing the pane is what keeps a five-agent run the same size
as a one-agent run:

```bash
for name in audit-api audit-web audit-jobs; do
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
  [ -n "$SURF" ] || { echo "no surface for $name" >&2; continue; }
  SID=$(uuidgen | tr 'A-Z' 'a-z')
  cmux send --workspace "$WS" --surface "$SURF" "cd $REPO && claude --session-id $SID -n $name\n"
  printf '%s\t%s\t%s\t%s\t%s\n' "$SID" "$(surf_field "$SURF" id)" "$(surf_field "$SURF" pane_id)" "$name" spawned >> "$LEDGER"
done
```

**Do not collect the refs in a space-joined string** — `for s in $SURFS` iterates
once in zsh, not once per worker, and you will drive one agent while believing
you drove four. The ledger is the list: re-read it with
`while IFS=$'\t' read -r sid su pu name state` and resolve each ref with
`surf_field "$su" ref` when you need one.

A new surface also lands **after the selected tab**, not at the end, so never
infer which worker is which from tab order.

Don't wait on a fan-out one worker at a time. The single watcher below already
reports each `DONE` as it lands, keyed by its own ledger row, so a slow agent
never blocks reporting a fast one — and you never have to pick which one to
block on.

The user watches a fan-out by cycling that pane's tab bar, so **name the sessions
for the tab bar** (`-n audit-api`, `-n audit-web`): the title is all that
distinguishes four identical terminals.

## Wait for a stage to finish

A worker stays `idle` for a beat after you press Enter, so a bare "wait until not
busy" returns instantly and reports a stage that never ran. Take the edge from an
event, and use the registry for liveness.

There are two ways to wait, and **the push one is the default**. A polling loop
only runs while you are inside a turn, so any stage that outlives the turn is a
stage nobody is watching. That is how a run goes quiet: you do not decide to stop
watching, you simply never get called again. Push does not rely on you
remembering.

### Push — let completion interrupt you

Spawned workers already feed cmux's event bus; the Claude wrapper injects the
hooks, so there is nothing to install. `agent.hook.Stop` carries
`payload.session_id` as `claude-<the id you assigned at spawn>` — which is the
whole reason for assigning that id.

Arm **one** `Monitor` right after the first spawn — always, including for a run of
exactly one worker. It covers every worker in the run, including ones spawned
later, because the filter re-reads the ledger when it changes:

```bash
cmux events --category agent --no-heartbeat --reconnect \
| python3 -u "${CLAUDE_PLUGIN_ROOT}/skills/cmux-spawn-agent/watch-workers.py" \
    "${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"
```

**That last line spells the ledger path out on purpose — do not shorten it to
`"$LEDGER"`.** `Monitor` runs this in a shell of its own, which never saw the
assignment you made in some earlier `Bash` call. `$LEDGER` there expands to the
empty string, the watcher `stat`s nothing, its ledger reads as zero rows, and every
worker is filtered out as unknown. You get a watcher that runs happily and reports
nothing — indistinguishable from a run where nothing has finished yet, and the same
silent-deafness failure the timestamp fallback exists to prevent.

`TMPDIR` and `CMUX_SURFACE_ID` *are* in that shell's environment, which is why the
expanded form is safe where the variable is not.

That path is substituted when this skill loads, so by the time you read it it is
already an absolute path to this plugin's own copy of the watcher — run it as
written rather than searching for the file. It is **not** an environment variable
at that point: `echo "$CLAUDE_PLUGIN_ROOT"` from a shell prints nothing, so never
build the path yourself from that variable.

Each output line becomes a chat notification that re-invokes you:

- `DONE <name>` — that worker's turn ended.
- `ATTN <name>` — blocked mid-turn on a permission prompt. **Not** an ending.
- `EXIT <name>` — the session ended.

Pass `persistent: true` for a run that may outlast a single Monitor timeout.

Four verified behaviours are already handled in that filter. Do not "simplify"
them away:

- **One turn end is four frames.** Each hook arrives as a `received`/`completed`
  pair, then again ~275 ms later once the surface resolves. Without the 5 s
  collapse window you report three completions for one stage.
- **`agent.hook.Notification` fires on normal finishes too**, so it cannot mean
  "needs attention". `agent.hook.PermissionRequest` is the precise mid-turn block.
- **Filter by the ledger's session ids, always.** The bus is global across
  workspaces and every config profile, so an unfiltered watcher fires on tabs the
  user owns — and on your own turns, which is a self-trigger loop.
- `notification.created` events have `title`/`subtitle`/`body` **redacted** to
  lengths. The event is the trigger; `cmux list-notifications` is still how you
  read what it actually said.

**There is no lighter option for one worker.** A backgrounded
`cmux events --name agent.hook.Stop … | grep -m1 "claude-$SID"` looks like it
gives you the single notification you need, and it is wrong twice over:

- **It can only see turn *ends*.** `PermissionRequest` and `SessionEnd` are not
  `Stop`, so a worker blocked on an approval prompt and a worker that died are
  both indistinguishable from one still working. You wait, it waits, nothing
  reports. The three-way `DONE`/`ATTN`/`EXIT` split is the entire point of the
  filter, and this throws it away to save one process.
- **`grep -m1` does not reliably end the pipeline.** It stops reading at the
  match, but `cmux events` only learns that on its *next* write, so the command
  can sit there matched-but-not-exited — and a completion notification that
  depends on the command exiting never fires. Observed: the match sat in the
  output file while the run went silently unreported.

One worker still gets one `Monitor` and the filter above. A one-row ledger is a
valid ledger.

### Poll — only when you need the answer inside this turn

`$SURF_UUID` below is the surface **UUID** assigned at spawn (`surf_field "$SURF"
id`) — column 2 of the ledger, the `su` you read back when waiting on a fan-out.
It is not interchangeable with the `surface:N` ref: notifications carry only the
UUID, so a ref — or an unset variable — matches nothing and `newest` returns
empty forever. That failure is silent and looks exactly like "no notification
yet", so the loop below never breaks on a **healthy** agent and exits only when
the worker dies.

```bash
newest() { cmux list-notifications --json | python3 -c "
import json,sys
d=json.load(sys.stdin); d=d if isinstance(d,list) else d.get('notifications',[])
m=[n for n in d if n.get('surface_id','').lower()=='$SURF_UUID'.lower()]
print(m[0]['id'], m[0]['subtitle'], sep='\t') if m else print()"; }

IFS=$'\t' read -r BEFORE _ <<<"$(newest)"   # capture BEFORE sending the prompt
# ... send + send-key enter ...
while :; do
  IFS=$'\t' read -r id sub <<<"$(newest)"
  if [ -n "$id" ] && [ "$id" != "$BEFORE" ]; then
    case "$sub" in
      Permission*) echo "$SURF wants approval"; BEFORE=$id ;;   # mid-turn, not an ending
      *) break ;;
    esac
  fi
  state=$(claude agents --json | python3 -c "
import json,sys
print(next((a['status'] for a in json.load(sys.stdin) if a['sessionId']=='$SID'),'gone'))")
  [ "$state" = gone ] && { echo "worker died before finishing" >&2; break; }
  sleep 2
done
```

**Budget the loop by its real cadence, not by the `sleep`.** Each iteration
shells out twice (`cmux list-notifications` + `claude agents`), measured at
~400 ms on top of the `sleep 2` — so the true period is ~2.4 s, and an iteration
count sized as `N × 2 s` overruns its budget by 20%. At a 300 s harness ceiling
that means ~125 iterations, not 150; exceed it and the call is SIGKILLed
(`exit 143`) with the loop's findings still unprinted. For waits longer than one
call, don't stretch the loop — poll in bounded chunks, or watch the worker's
child pids from a backgrounded `until` loop and let it notify you once.

`status` is a read-only variable in zsh — name it anything else. The list keeps
only the **newest notification per surface** and replaces it in place, so compare
ids rather than counting, tolerate a momentarily empty read, and never expect
history: closing a surface takes its notification with it.

The `subtitle` is what tells the three cases apart, so read it before anything
else:

- `Completed in <dir>` — the turn ended; `body` carries the final message.
- `Waiting` — the agent handed the turn back. A normal finish usually fires both,
  `Completed` first.
- `Permission` (body: `Claude needs your permission`) — a **mid-turn** approval
  prompt, not an ending. Each one is a fresh notification id, so a loop that
  breaks on "any new id" will report a stage that is still running. Rebase your
  cursor on it and keep waiting, as above.

**A worker's input box may show text nobody typed.** `read-screen` renders Claude
Code's suggested-follow-up ghost text in the prompt exactly like real input, so a
supervisor falling back to the screen can read it as a pending user message, or as
unsent input it ought to clear. It is neither: it is a suggestion, and sending
`enter` would submit it. Judge from the transcript or the event, and treat prompt
contents as decoration.

**`Waiting` on its own is still ambiguous** — a finished agent and one parked on
a prompt both reach it, and `claude agents` says `idle` either way. When it
matters, read the screen (`cmux read-screen --workspace "$WS" --surface <ref>`) or the transcript
rather than guessing from the event.

## Chain stages

Same session, next command (context carries over — use this when the next stage
needs what the previous one just found):

```bash
cmux send --workspace "$WS" --surface "$SURF" "/spec-write <what to write up>"
cmux send-key --workspace "$WS" --surface "$SURF" enter
```

Fresh session for a stage that should start clean: another surface in the same
agents pane, exactly as above — a new stage is never a reason for a new split. It
shares no context, so **pass the handoff explicitly** — name the file the
previous stage wrote, rather than referring to "the findings."

## Read a worker's output

```
<config-dir>/projects/<esc-cwd>/<sessionId>.jsonl
```

`esc-cwd` replaces **every** non-alphanumeric character with `-`
(`/Users/you/work/stock_check_app` → `-Users-you-work-stock-check-app`).
Records are `{"type": "assistant", "message": {...}}` in the Anthropic message
shape — `content`, `stop_reason`, `usage`.

**The file does not exist until the session takes its first turn.** A freshly
spawned worker has a registry entry and no transcript; handle that rather than
assuming the path resolves. The path follows the worker's *actual* cwd, so read
`cwd` from `claude agents --json` instead of assuming it matched your `cd`.

## Several config profiles — check before spawning

`claude agents --json` reads only the active `CLAUDE_CONFIG_DIR`. A worker
launched into a directory that selects a different profile — a shell hook that
switches `CLAUDE_CONFIG_DIR` per directory is a common setup — registers there
and nowhere else, so a supervisor watching one registry will report it as
**gone**. Search every profile you use:

```bash
claude agents --json                                       # default profile
CLAUDE_CONFIG_DIR=~/.claude-<other> claude agents --json   # each additional profile
```

Read its transcript from the matching profile's `projects/` tree too.

Plugins are installed per profile, so **the command a stage needs may not exist
in the profile its directory selects**. Check the installed plugins for the
relevant profile (`claude plugin list`, or `enabledPlugins` in that profile's
`settings.json`) before building a pipeline on a slash command.

## Offer to close what the run opened

When the last stage is reported, the ledger rows are spent surfaces. Resolve each
one, then **offer** — cleanup is a proposal, never a side effect of finishing:

```bash
while IFS=$'\t' read -r sid su pu name state; do
  ref=$(surf_field "$su" ref)
  [ -n "$ref" ] || continue               # the user already closed it; drop the row
  reg=$(claude agents --json | python3 -c "
import json,sys
print(next((a['status'] for a in json.load(sys.stdin) if a['sessionId']=='$sid'),'gone'))")
  alive=no; ps -t "$(surf_field "$su" tty)" -o command= 2>/dev/null | grep -q '[c]laude' && alive=yes
  echo "$ref  $name  ledger=$state  registry=$reg  claude=$alive"
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
  (`surf_field "$su" ref` returns empty once it is gone), never by reading that ref.
- Closing a tab with siblings left re-selects the previously visible one and
  moves no focus. Closing the **last** one collapses the pane, and focus lands on
  the pane it was split off from — yours. Harmless, but say so when you offer, in
  case the user is reading a third pane at the time.
- The scrollback dies with the surface; the session does not.
  `claude --resume <sessionId>` brings it back, so say that when you ask.
- `busy`, or a `Waiting` notification you have not read yet, means not done.
  Leave those open and say which ones you left and why.
- `registry=gone` with `claude=no` is the true orphan: a dead worker sitting in a
  bare shell. Lead with those.
- Prune closed rows from the ledger so the next offer is not a list of ghosts.

Only ever propose surfaces from this run's ledger. A tab you did not spawn is the
user's, however idle it looks — leave it alone even when it is obviously a dead
agent from an earlier session.

### Finish the run — four steps, in this order

Closing the surfaces is not the end. The watcher is a *process*, and a `Monitor`
armed with `persistent: true` runs until `TaskStop` or the end of the session that
armed it — and **if you are yourself a spawned agent, your session ending does not
reap it.** Observed: an agent finished, its surface was closed, and its watcher was
still streaming the global event bus.

Do these in order. The order matters: stopping the watcher before deleting the ledger
leaves a window where a late event names a worker you have already reported.

1. **Close each surface** you spawned, as above — resolving by UUID, never by the
   `OK surface:N` echo.
2. **Delete the ledger file**, not just its rows:
   `rm -f "${TMPDIR:-/tmp}/cmux-spawn-agent/${CMUX_SURFACE_ID}.tsv"`. This is the
   belt-and-braces step — a watcher that somehow survives with no ledger matches no
   session id and reports nothing, so it is inert rather than wrong.
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

- Anchor placement on `$CMUX_WORKSPACE_ID` and `$CMUX_SURFACE_ID`, never on what
  is focused — the user may be looking elsewhere. Pass `--focus false`.
- One split per run, at most. Every agent after the first is a tab.
- **Arm the watcher at the first spawn, before sending any task** — one `Monitor`
  running `watch-workers.py` against the ledger, for one worker or for ten. A poll
  loop dies with the turn; a run that outlives it is a run nobody is watching. Any
  hand-rolled substitute that greps the bus directly is blind to `ATTN` and `EXIT`,
  which is to say blind to every way a run fails.
- **The ledger is on disk**, at
  `${TMPDIR:-/tmp}/cmux-spawn-agent/<caller surface id>.tsv` — keyed by the
  caller's surface, so it is exactly this run's spawns and nothing else. That is
  what lets a turn which remembers nothing still answer what it owes and what it
  may close.
- Report each stage's outcome as it lands; don't go silent for a long pipeline.
  Mark the ledger row `reported` when you do, so an interrupted run can still
  answer what it owes.
- Never close a surface you did not spawn, and never close one without asking —
  not even your own.
- **A run ends when its watcher is stopped, not when its last tab closes.** A
  persistent `Monitor` outlives the session that armed it, so a spawned agent that
  arms one and exits leaves it streaming. `TaskStop` it and delete the ledger.
- Before a destructive or long-running task, say which repo and which profile it
  will run in and get confirmation.
- Workers inherit the user's global `~/.claude/CLAUDE.md`. If you plan to parse a
  worker's output, expect whatever that file makes every session emit.
- `cmux new-surface --type agent-session --provider claude` also opens a Claude
  surface, but nothing lets you pre-assign its session id — which is the whole
  lever here. Spawn terminals.
