#!/usr/bin/env python3
"""Answer "who is sitting in this slot right now" before you write to it.

Usage: occupant.py <tty> <ledger.tsv> <name>

  exit 0   the slot holds this run's worker, or holds no `claude` at all
  exit 3   the slot holds a `claude` this run did not start -- HARD STOP
  exit 4   the row carries nothing that could identify the `claude` in there,
           so this run cannot claim the slot either -- HARD STOP
  exit 2   usage

**0 is the only pass.** Test for zero, never for one particular non-zero code: 3
and 4 are two diagnoses of the same refusal, and a caller that special-cases one
of them turns the other into a pass on the day it first fires.

WHY THIS EXISTS AND WHY `owned.py` IS NOT ENOUGH.

`owned.py` answers a question about a *session*: is the process behind this row
ours. Every command that actually writes to a terminal asks a different question,
about a *slot*: cmux `send`, `send-key`, `read-screen` and `close-surface` all take
a surface, and herdr's pane verbs take a pane — none of them take a session id, and
none of them consult a registry.

Those two questions come apart, and the gap is not exotic. A slot is durable by
design: it outlives any one `claude`, which is what lets a supervisor quit and
relaunch in the same place. So:

    1. the run spawns `review-api` into surface U and records U in the row
    2. the worker exits -- cmux keeps surface U, now a bare shell
    3. the user types `claude` in that tab
    4. U still resolves, so every locator-addressed command still "works"

At step 4 `owned.py` correctly says our worker is gone, and that is exactly what
makes it dangerous: the cleanup section reads `registry=gone` as "died mid-stage,
lead with those", offers the slot, and `close-surface` kills the session the user
just started. The same locator drives the `send-key` used to answer a blocked
worker, and the `cmux send` used to deliver a slash command -- so the identical
staleness types work into a human's session instead of closing it.

The join is the controlling terminal. A host can name the tty behind a slot
(`cmux-surface.py <uuid> tty`), and every `claude` has one, so the question is
decidable with `ps` alone -- no host API, nothing to authenticate, and it works
identically for a worker that was never ours. This check is deliberately
host-blind: a tty is a tty on either host.

TWO JOINS, BECAUSE THE PIN ARRIVES LATE.

The pid in column 6 is captured at *readiness*, once the minted id has identified
a registered session. Before that the column holds a literal `-`, which compares
unequal to every real pid -- so a check that joins only on the pid calls this
run's own worker a stranger for the whole of the launch window.

That window is not a corner. It is exactly where the **folder-trust gate** lives,
and a keystroke is the only thing that clears it. `SKILL.md` mandates this check
before every send, keystroke, screen read and close and calls exit 3 a hard stop,
so read literally the guard forbade the one act that lifts the gate: a worker
parked on "Is this a project you created or one you trust?" could not be answered
by the run that launched it. Found by /spawn-agent-smoke on 2026-08-17.

The `-` placeholder did not cause this and changing it does not fix it. Measured
2026-08-17 against one live tty with two ledger fixtures identical but for their
width: the 7-column row pinning `-` and the 6-column row pinning the empty string
**both exit 3**, differing only in whether the message reads `pins pid -` or
`pins pid <none>`. Every unpinned row has always read as a stranger.

So an unpinned row joins on the argv instead. A gated `claude` has registered
nothing -- there is no record anywhere to look it up in -- but its command line
still carries `--session-id <the uuid this run minted>`, and **a human never
passes that flag.** That is not a new trust assumption. It is the one the whole
ownership design already rests on -- "only ever touch what this run minted" --
applied one layer earlier, to a process that does not yet have a session to look
up.

    column 6 is a pid          join on the pid, as before
    column 6 is anything else  join column 5 against the argv on that tty
    column 5 is empty too      nothing to join on at all -> exit 4

The second join costs no extra `ps`: `claude_procs_on` already had the whole
command line in its hand and threw it away after reading argv0.

WHY A ROW THAT CAN PROVE NOTHING GETS ITS OWN CODE.

A 4-column row predates ownership entirely; a name with no row at all is weaker
still. Neither says anything about the `claude` on that tty, and neither improves
by being retried. Reporting that as a *stranger* would be wrong in the direction
that costs something: it names the slot as the problem when the problem is the
row, and the repair is a different one -- fix or abandon the row, and never close
a slot you cannot attribute. Hence a separate code, chosen above the stop
threshold every documented consumer already uses, so telling the two apart is
possible without making either of them survivable.

MATCHING THE ID, AND ASKING `ps` FOR THE WHOLE LINE.

The comparison is case-insensitive -- the CLI accepts any 8-4-4-4-12 hex string
and preserves its case verbatim, so `owned.py` compares registry records the same
way. It is a whole-token match, split on `=` as well as whitespace, so
`--session-id=<uuid>` counts while a uuid that merely appears inside a longer path
does not.

`-ww` is not decoration. Measured on this machine 2026-08-17, a live `claude`'s
command line is **2021 characters and `--session-id` begins at column 1973**,
because the host injects a `--settings` JSON blob ahead of it. Any width-limited
`ps` would cut the flag off and every unpinned worker would read as a stranger.
The flag is a no-op today -- output goes to a pipe and this `ps` drops its width
limit when stdout is not a terminal, measured byte-identical with and without it
-- but that pipe now carries the tail of the line rather than just argv0, and it
is cheaper to ask for the width than to rely on every reader knowing why it is
already there.

An EMPTY slot is deliberately exit 0. A closed-then-reopened tab with only a shell
in it is still ours to tidy up, and refusing there would leak a slot on every run
whose worker exited cleanly.
"""

import os
import subprocess
import sys


def claude_procs_on(tty):
    """(pid, command line) for every live `claude` whose controlling tty is this."""
    r = subprocess.run(
        ["ps", "-ww", "-t", tty, "-o", "pid=,command="], capture_output=True, text=True
    )
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        argv0 = (cmd.split() or [""])[0]
        # Match the executable, not the word: a shell running `grep claude` or an
        # editor holding SKILL.md open must not read as an agent in the slot.
        if os.path.basename(argv0).startswith("claude"):
            try:
                out.append((int(pid), cmd))
            except ValueError:
                pass
    return out


def row_for(ledger, name):
    """(minted session id, pinned pid) for `name` -- ("", "") when unreadable.

    An absent row and a row that carries neither field answer the same, because
    they are the same answer: this ledger cannot identify anybody in that slot.
    """
    try:
        with open(ledger) as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if cols and cols[0].strip() == name:
                    sid = cols[4].strip() if len(cols) >= 5 else ""
                    pid = cols[5].strip() if len(cols) >= 6 else ""
                    return sid, pid
    except OSError:
        pass
    return "", ""


def carries(cmd, sid):
    """True when this command line passes `sid` as an argument value."""
    want = sid.lower()
    return any(tok.lower() == want for tok in cmd.replace("=", " ").split())


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: occupant.py <tty> <ledger.tsv> <name>")
    tty, ledger, name = sys.argv[1], sys.argv[2], sys.argv[3]
    if not tty:
        # An empty tty is the resolver saying the slot is gone. Refuse rather than
        # let `ps -t ""` answer about something else entirely.
        print("occupant: no tty for that slot -- resolve it before writing to it",
              file=sys.stderr)
        return 3

    procs = claude_procs_on(tty)
    if not procs:
        return 0  # a bare shell: ours to close, nothing to hijack

    minted, pinned = row_for(ledger, name)

    if pinned.isdigit():
        strangers = [p for p, _ in procs if str(p) != pinned]
        held = f"pins pid {pinned}"
    elif minted:
        # Pre-pin: the process exists, the session does not. Ask its argv.
        strangers = [p for p, cmd in procs if not carries(cmd, minted)]
        held = f"is unpinned, and those do not carry its minted id {minted}"
    else:
        print(
            f"occupant: row {name!r} carries neither a pinned pid nor a minted "
            f"session id, so nothing in it can identify the claude on {tty} "
            "-- do not send, key, or close it, and do not retry",
            file=sys.stderr,
        )
        return 4

    if not strangers:
        return 0

    print(
        f"occupant: {tty} is running claude pid(s) "
        f"{', '.join(str(p) for p in strangers)}, which this run did not start "
        f"(row {name!r} {held}) -- do not send, key, or close it",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    sys.exit(main())
