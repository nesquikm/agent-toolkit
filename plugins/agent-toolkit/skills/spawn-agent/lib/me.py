#!/usr/bin/env python3
"""Print this session's own identity: name, uds: address, and session id.

Usage: me.py            ->  <name>\\t<uds:address>\\t<sessionId>
       me.py sessionId  ->  one field (name | address | sessionId)

Three things a session cannot look up about itself through any tool, and all three
are needed:

  name        goes in a worker's task text as a label, because a worker asked for
              "the name you were launched with" cannot find it -- measured 2 of 2
              guessing a name belonging to a different, concurrent run
  address     goes in the task text as the literal reply target, because "use the
              from address on this message" is not sufficient: 2 of 2 workers told
              exactly that addressed the supervisor by name anyway and were refused
  sessionId   goes in the ledger's .owner sidecar, which is what lets a worker's
              reply be recognised as a reply rather than as an unowned send

IT WALKS THE PROCESS TREE; IT DOES NOT TAKE THE PARENT OF THE SHELL. The naive
`ps -o ppid=` form is right only when `claude` is the *immediate* parent of the
shell a Bash call got. Put any wrapper in between and it names the wrapper.
Measured 2026-08-12 in cmux, session `fixer-b2`, claude pid 27957:

    direct              parent of the shell is 27957 -> .../claude    ok
    through one zsh -c  parent of the shell is 36470 -> /bin/zsh      empty
    the walk, wrapped   resolved claude pid 27957                     ok

The walk returned that same answer at zero, one and two extra shell levels.

EVERY FAILURE EXITS NON-ZERO WITH A MESSAGE, and never prints an empty string.
`sessions/<pid>.json` is keyed by pid, and pids are recycled into exactly the
range wrapper shells are born in -- so a wrapper whose pid happens to match some
other live session's record resolves to that session, in full. Measured
2026-08-12: an earlier form handed pid 19143 printed a complete, plausible, wrong
answer naming a live session belonging to somebody else's run. The walk cannot
produce that, because it only ever reads the record of a pid it has confirmed is
a `claude`.

Run from inside a subagent it resolves the HOSTING CLI session, by construction --
that is what the walk looks for.
"""

import json
import os
import subprocess
import sys

# `peer.py` owns the profile list, and every other script here routes through it
# -- owned.py's registry_roots() returns peer.roots() verbatim, and the watcher
# rebuilds only the pair shape on top of it. This file forked that list once and
# searched CLAUDE_CONFIG_DIR's segments OR `~/.claude`, never both and never the
# `~/.claude-*` glob, which is the same drift owned.py records fixing on
# 2026-09-07. Imported by path for owned.py's reason: sys.path[0] is the lib
# directory only when the interpreter was pointed straight at this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import peer  # noqa: E402

FIELDS = ("name", "address", "sessionId")


def ps(fmt, pid):
    r = subprocess.run(
        ["ps", "-o", fmt, "-p", str(pid)], capture_output=True, text=True
    )
    return r.stdout.strip()


def claude_ancestor(pid):
    for _ in range(8):
        argv0 = (ps("command=", pid).split() or [""])[0]
        if os.path.basename(argv0).startswith("claude"):
            return pid
        parent = ps("ppid=", pid)
        if not parent or int(parent) <= 1:
            sys.exit(f"me: no claude ancestor above pid {os.getppid()}")
        pid = int(parent)
    sys.exit(f"me: no claude ancestor within 8 levels of pid {os.getppid()}")


def main():
    field = sys.argv[1] if len(sys.argv) > 1 else None
    if field is not None and field not in FIELDS:
        sys.exit(f"usage: me.py [{' | '.join(FIELDS)}]")

    pid = claude_ancestor(os.getppid())

    # Every profile a session may register in -- peer.py's list, not a copy, so
    # this file agrees with owned.py, the watcher and the guard about where a
    # session may live. Honouring CLAUDE_CONFIG_DIR's segments is necessary and
    # was never sufficient: the case that matters is a session whose record lands
    # in a profile CLAUDE_CONFIG_DIR does NOT name, which is the ordinary shape
    # here -- a worker does not register in its supervisor's profile (owned.py's
    # registry_roots records that measurement), and a chpwd hook that switches
    # CLAUDE_CONFIG_DIR per directory can make it true of a supervisor too.
    #
    # Widening cannot select a stranger: the lookup below is keyed by a pid
    # already confirmed to be this session's own `claude` ancestor. Ordering is
    # peer.roots()'s -- active profile first -- and first match wins, so the
    # active profile still decides if a recycled pid is stale somewhere else.
    roots = peer.roots()
    for root in roots:
        try:
            with open(os.path.join(root, "sessions", "%d.json" % pid)) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        sock = rec.get("messagingSocketPath")
        if not sock:
            # Refused for the same reason the collision check refuses it: a record
            # without a socket cannot be replied to at all.
            sys.exit(f"me: session {pid} is registered without a messaging socket")
        out = {
            "name": rec.get("name", ""),
            "address": "uds:" + sock,
            "sessionId": rec.get("sessionId", ""),
        }
        if not out["sessionId"]:
            sys.exit(f"me: session {pid} has no sessionId")
        print(out[field] if field else "\t".join(out[f] for f in FIELDS))
        return 0
    where = ":".join(roots) or "no profile on this machine has a sessions/ directory"
    sys.exit(f"me: no session record for claude pid {pid} under {where}")


if __name__ == "__main__":
    sys.exit(main())
