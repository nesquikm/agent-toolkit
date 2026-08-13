#!/usr/bin/env python3
"""Answer "who is sitting in this slot right now" before you write to it.

Usage: occupant.py <tty> <ledger.tsv> <name>

  exit 0   the slot holds this run's worker, or holds no `claude` at all
  exit 3   the slot holds a `claude` this run did not start -- HARD STOP
  exit 2   usage

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
(`cmux-surface.py <uuid> tty`), and every `claude` has one, so "is the claude on
this tty the pid in column 6" is decidable with `ps` alone -- no host API, nothing
to authenticate, and it works identically for a worker that was never ours.

An EMPTY slot is deliberately exit 0. A closed-then-reopened tab with only a shell
in it is still ours to tidy up, and refusing there would leak a slot on every run
whose worker exited cleanly.
"""

import os
import subprocess
import sys


def claude_pids_on(tty):
    """Live `claude` pids whose controlling terminal is this tty."""
    r = subprocess.run(
        ["ps", "-t", tty, "-o", "pid=,command="], capture_output=True, text=True
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
                out.append(int(pid))
            except ValueError:
                pass
    return out


def pinned_pid(ledger, name):
    try:
        with open(ledger) as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if cols and cols[0].strip() == name and len(cols) >= 6:
                    return cols[5].strip()
    except OSError:
        pass
    return ""


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

    pids = claude_pids_on(tty)
    if not pids:
        return 0  # a bare shell: ours to close, nothing to hijack

    mine = pinned_pid(ledger, name)
    strangers = [p for p in pids if str(p) != mine]
    if not strangers:
        return 0

    print(
        f"occupant: {tty} is running claude pid(s) "
        f"{', '.join(str(p) for p in strangers)}, which this run did not start "
        f"(row {name!r} pins pid {mine or '<none>'}) -- do not send, key, or close it",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    sys.exit(main())
